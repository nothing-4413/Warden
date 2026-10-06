#!/usr/bin/env python
"""检索评测：自建中文标注语料，报 Hit@1 / Hit@3 / Hit@4 与 MRR。

需要 OpenAI 兼容端点（Warden 的 ``llm_base_url``）提供 ``/embeddings``；
``--compare`` 还会调 ``/chat/completions`` 跑「查询改写 + LLM 重排」对照。

用法::

    python -m scripts.eval_retrieval                    # 纯向量
    python -m scripts.eval_retrieval --compare          # 再跑改写+重排，给 ablate 差值
    python -m scripts.eval_retrieval --k 4 --out data/eval/retrieval.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.memory.embeddings import EmbeddingClient  # noqa: E402
from app.memory.indexer import NotesIndexer  # noqa: E402
from app.memory.retriever import Retriever  # noqa: E402
from app.memory.vector_store import VectorStore  # noqa: E402

# 20 篇互不相同的笔记。主题刻意相邻（Python / SQLite / 容器 / 可观测 / 检索），
# 词汇互相干扰，避免「关键词撞上就算命中」。
CORPUS: list[tuple[str, str, str]] = [
    (
        "async-io",
        "异步编程",
        "事件循环负责调度待执行的任务，遇到等待输入输出的地方用 await 把控制权交回去，"
        "同一个线程就能在等待期间去干别的事。协程函数本身不会立刻执行，调用它只是造出一个协程对象，"
        "要交给调度器才会跑起来。同步库里阻塞调用的函数放进这种模型里会卡住整个循环，"
        "要用线程池包一层。",
    ),
    (
        "packaging",
        "打包发布",
        "项目元数据写在 pyproject.toml 里，构建后端负责把源码变成可安装的分发包。"
        "构建产物放在 dist 目录，安装测试用本地路径装一遍再决定要不要上传。"
        "上传前要确认版本号没被占用，重复上传同名同版本会被仓库直接拒绝。",
    ),
    (
        "type-hints",
        "类型注解",
        "在函数签名上标出参数与返回值的类型，静态检查器就能在运行之前发现传错类型的地方。"
        "联合类型用竖线连接，可选值写成与 None 的联合。"
        "注解在运行时默认不强制，配置严格的检查模式可以把隐式宽松也拦下来。",
    ),
    (
        "unit-test",
        "单元测试",
        "把可复用的准备与清理逻辑抽成夹具，测试函数按名字声明自己需要哪一个。"
        "同一段逻辑需要多组输入时用参数化，每组输入单独算一个用例，失败时能直接看到是哪一组。"
        "断言尽量只查一个事实，覆盖率报告只用来找没被跑到的分支。",
    ),
    (
        "sqlite-wal",
        "SQLite 并发写",
        "默认的回滚日志模式下写操作会把整库锁住，读要等写做完。"
        "换成预写日志模式后读不再被写阻塞，但同一时刻仍然只允许一个写入者，"
        "多个进程同时写就会拿到「数据库被锁」的错误，需要重试或把写集中到一个进程。",
    ),
    (
        "sqlite-plan",
        "查询计划",
        "在语句前面加上解释前缀，就能看到执行器打算用哪个索引、扫多少行。"
        "出现全表扫描通常说明条件列上没有可用的索引，或者表达式把列包住让索引失效了。"
        "复合索引的列顺序要和查询里的过滤顺序对得上才有用。",
    ),
    (
        "image-layer",
        "镜像分层",
        "每条构建指令产生一层，层按内容做指纹缓存。"
        "把变化最频繁的源码复制放到最后，前面装依赖的步骤就能在改代码时继续命中缓存。"
        "忽略清单里漏掉临时目录，会让每次构建的指纹都变，缓存全废。",
    ),
    (
        "container-net",
        "容器网络",
        "同一个编排网络里的服务可以直接用服务名互相访问，名字由内置解析提供。"
        "容器里的回环地址指向容器自己，要访问跑在宿主机上的进程得用特殊的宿主机别名。"
        "端口映射只影响从外部进来的流量，容器之间通信不需要它。",
    ),
    (
        "health-probe",
        "健康检查",
        "存活探针用来判断进程是不是卡死，失败次数到了就重启容器；"
        "就绪探针判断能不能接流量，失败只会把实例从负载均衡里摘掉。"
        "探针超时设得比启动时间还短，会让服务在初始化阶段被反复重启。",
    ),
    (
        "metrics",
        "指标类型",
        "只增不减的累计量适合用计数器，可上可下的瞬时值用仪表盘，"
        "要算分位数就用直方图并提前分好桶边界。"
        "分位数不能跨实例求平均，只能在查询时聚合。",
    ),
    (
        "struct-log",
        "结构化日志",
        "把字段以键值对形式输出，采集端就不用写正则去猜。"
        "给每条日志带上请求标识，跨多个服务的处理链路才能串起来。"
        "日志级别别乱用，把正常流程打成错误会让告警失去意义。",
    ),
    (
        "git-rebase",
        "变基",
        "把当前分支的提交挪到目标分支最新提交之上，历史会变成一条直线。"
        "冲突时逐个提交解决，解决完继续，想退出就中止，仓库会回到操作前的状态。"
        "已经推送到公共分支的提交不要随便改写，别人拉下来会重复。",
    ),
    (
        "git-hook",
        "提交钩子",
        "在提交前自动跑格式化与静态检查，能在本地拦住低级的样式问题。"
        "钩子脚本要能自己找到项目依赖，否则换台机器就失效。"
        "服务端的钩子不能被客户端绕过，本地的可以加参数跳过。",
    ),
    (
        "idempotency",
        "幂等重试",
        "请求带上客户端生成的唯一键，服务端第一次处理完就把结果按这个键存起来。"
        "重试时带上同一个键，服务端发现已经处理过就直接把上次的结果返回，不会重复扣费。"
        "键必须由确定性的输入派生，用随机数或当前时间做键等于每次都新建一次操作。",
    ),
    (
        "semantic-cache",
        "语义缓存",
        "把请求的向量和答案一起存下来，新请求先算相似度，超过阈值就认为语义相同、直接返回缓存。"
        "阈值调低能多命中，但会把意思不同的请求也判成同一个，出现答非所问。"
        "只对文字长度足够的请求启用，短问句的向量区分度太差。",
    ),
    (
        "embedding-choice",
        "嵌入模型",
        "向量维度决定索引占用与检索速度，维度过高的收益往往不如多切几段文本。"
        "中文语料要确认模型真的在中文上训练过，否则同义句的相似度会不稳定。"
        "换模型等于换向量空间，旧库里存下的向量必须重建。",
    ),
    (
        "chunking",
        "文本切块",
        "块太大时一个向量要代表好几个话题，检索命中的段落里只有一部分相关；"
        "块太小时上下文被切断，模型拿不到完整信息。"
        "相邻块之间留一点重叠可以避免答案正好落在切口上。",
    ),
    (
        "token-cost",
        "调用计费",
        "费用按输入与输出两个方向分别计价，输出通常更贵。"
        "把长文档反复塞进提示词会线性推高输入费用，缓存或摘要能压下来。"
        "统计时要按实际返回的用量字段算，不要用字符数粗略估算。",
    ),
    (
        "zettel",
        "卡片笔记",
        "一条笔记只放一个想法，用自己的话写，方便以后被复用。"
        "笔记之间用双向链接连起来，时间久了会长出主题索引。"
        "收集不等于理解，定期回看并重写才能把材料变成自己的东西。",
    ),
    (
        "sleep-log",
        "作息记录",
        "每天固定时间上床比单纯延长睡眠时长更能改善白天的状态。"
        "记录入睡与起床时间、白天困倦程度，两周后就能看出规律。"
        "咖啡因的半衰期比想象中长，午后摄入会影响夜间深睡。",
    ),
]

# (查询, 正确文档 id) —— 查询是释义化的说法，尽量不出现标题里的原词。
QUERIES: list[tuple[str, str]] = [
    ("怎么让一个线程在等网络返回的时候去处理别的请求", "async-io"),
    ("协程对象造出来为什么没有立刻执行", "async-io"),
    ("把自己写的库做成别人能一条命令装上的东西", "packaging"),
    ("版本号重复上传会被仓库怎么处理", "packaging"),
    ("怎么在代码跑起来之前就发现参数传错了类型", "type-hints"),
    ("可选值在签名里应该怎么写", "type-hints"),
    ("一组输入要重复跑同一段逻辑该怎么组织", "unit-test"),
    ("准备和清理的数据怎么在多个用例之间复用", "unit-test"),
    ("多个进程同时往一个单文件数据库写会报什么错", "sqlite-wal"),
    ("读写互相阻塞的问题怎么解决", "sqlite-wal"),
    ("怎么知道一条查询是不是走了索引", "sqlite-plan"),
    ("为什么给列建了索引还是全表扫描", "sqlite-plan"),
    ("改一行代码不想重新装一遍依赖", "image-layer"),
    ("构建缓存为什么老是失效", "image-layer"),
    ("容器里的服务要访问宿主机上的进程用什么地址", "container-net"),
    ("同一个网络里的两个服务怎么互相找到对方", "container-net"),
    ("怎么判断服务能不能开始接收流量", "health-probe"),
    ("进程卡死了怎么让编排系统自动重启它", "health-probe"),
    ("要统计请求耗时的分位数该选哪种指标", "metrics"),
    ("分位数能不能跨多个实例求平均", "metrics"),
    ("怎么把一条请求在多个服务里的日志串起来", "struct-log"),
    ("采集端不想写正则解析日志该怎么办", "struct-log"),
    ("把提交挪到目标分支最新提交之上会发生什么", "git-rebase"),
    ("已经推到公共分支的提交还能改写吗", "git-rebase"),
    ("怎么在本地就拦住格式问题不让它进仓库", "git-hook"),
    ("服务端有没有不能被绕过的检查点", "git-hook"),
    ("重试的时候怎么保证不会重复扣费", "idempotency"),
    ("幂等键用随机数会有什么问题", "idempotency"),
    ("意思相近的请求怎么直接复用上次的答案", "semantic-cache"),
    ("相似度阈值调低会出现什么副作用", "semantic-cache"),
    ("向量维度越高检索效果就一定越好吗", "embedding-choice"),
    ("换了嵌入模型以后旧库还能用吗", "embedding-choice"),
    ("一个向量代表太多话题会有什么问题", "chunking"),
    ("为什么相邻文本块之间要留重叠", "chunking"),
    ("按什么口径统计调用花掉的钱", "token-cost"),
    ("长文档反复放进提示词会让哪部分费用变高", "token-cost"),
    ("怎样记笔记以后才方便复用", "zettel"),
    ("收集了很多材料但是记不住怎么办", "zettel"),
    ("为什么睡够了白天还是困", "sleep-log"),
    ("固定作息时间和延长睡眠哪个更有效", "sleep-log"),
]


def build(settings: Settings, db_path: Path) -> tuple[NotesIndexer, Retriever, VectorStore]:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = VectorStore(str(db_path))
    embedder = EmbeddingClient(settings)
    indexer = NotesIndexer(settings, embedder, store)
    for doc_id, title, text in CORPUS:
        indexer.index_text(f"# {title}\n\n{text}", doc_id)
    llm = LLMClient(settings)
    return indexer, Retriever(embedder, store, llm=llm), store


def evaluate(
    retriever: Retriever,
    settings: Settings,
    *,
    k: int,
    rewrite: bool,
    rerank: bool,
    verbose: bool = False,
) -> dict:
    hits = {1: 0, 3: 0, k: 0}
    rr = 0.0
    misses: list[dict] = []
    latencies: list[float] = []
    ranks: list[int] = []
    for query, want in QUERIES:
        started = time.perf_counter()
        chunks = retriever.retrieve(
            query, k=k, min_score=settings.retrieval_min_score, rewrite=rewrite, rerank=rerank
        )
        latencies.append((time.perf_counter() - started) * 1000)
        found = [c.doc_id for c in chunks]
        rank = found.index(want) + 1 if want in found else 0
        ranks.append(rank)
        if rank == 1:
            hits[1] += 1
        if 0 < rank <= 3:
            hits[3] += 1
        if 0 < rank <= k:
            hits[k] += 1
        rr += (1.0 / rank) if rank else 0.0
        if rank == 0:
            misses.append({"query": query, "want": want, "got": found})
            if verbose:
                print(f"  MISS  {query!r}\n        want={want} got={found}")
    n = len(QUERIES)
    latencies.sort()
    return {
        "queries": n,
        "k": k,
        "hit@1": hits[1] / n,
        "hit@3": hits[3] / n,
        f"hit@{k}": hits[k] / n,
        "mrr": rr / n,
        "latency_p50_ms": latencies[n // 2],
        "latency_p95_ms": latencies[min(n - 1, int(n * 0.95))],
        "ranks": ranks,
        "misses": misses,
    }


def hit_at(ranks: list[int], cutoff: int) -> float:
    """同一份排序下的 Hit@cutoff（rank 0 = 未召回）。"""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if 0 < r <= cutoff) / len(ranks)


def hit_curve(ranks: list[int], cutoffs: list[int]) -> dict[str, float]:
    """Hit@K 曲线：检索一次 k=max(cutoffs)，各截断点从同一份排序里数出来。

    这样各点之间可比（没有重新检索带来的噪声），也省掉 K 倍嵌入开销。
    """
    return {str(c): hit_at(ranks, c) for c in cutoffs}


def show(label: str, result: dict) -> None:
    k = result["k"]
    print(
        f"{label}: n={result['queries']} "
        f"Hit@1={result['hit@1']:.1%} Hit@3={result['hit@3']:.1%} "
        f"Hit@{k}={result[f'hit@{k}']:.1%} MRR={result['mrr']:.3f} "
        f"p50={result['latency_p50_ms']:.0f}ms p95={result['latency_p95_ms']:.0f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Warden 检索评测（Hit@K / MRR）")
    parser.add_argument("--db", default="data/eval_retrieval.db", help="评测用向量库路径")
    parser.add_argument("--k", type=int, default=4, help="检索条数（默认 4）")
    parser.add_argument(
        "--sweep",
        default="",
        help="Hit@K 曲线，逗号分隔截断点（如 1,3,4,8）；纯向量那组按最大截断点检索一次",
    )
    parser.add_argument("--compare", action="store_true", help="额外跑查询改写 + LLM 重排")
    parser.add_argument("--verbose", action="store_true", help="打印每条未命中")
    parser.add_argument("--out", default="", help="把结果写成 JSON")
    args = parser.parse_args()
    cutoffs = [int(x) for x in args.sweep.split(",") if x.strip()]
    eval_k = max([args.k, *cutoffs]) if cutoffs else args.k

    settings = Settings()
    if not settings.llm_base_url:
        print("llm_base_url 未配置", file=sys.stderr)
        return 2
    print(f"端点 {settings.llm_base_url} 嵌入模型 {settings.embedding_model} 语料 {len(CORPUS)} 篇")
    _, retriever, store = build(settings, Path(args.db))
    print(f"已索引 {store.count()} 块，查询 {len(QUERIES)} 条（k={eval_k}）")

    report: dict = {"endpoint": settings.llm_base_url, "embedding_model": settings.embedding_model}
    plain = evaluate(
        retriever, settings, k=eval_k, rewrite=False, rerank=False, verbose=args.verbose
    )
    report["vector_only"] = plain
    show("纯向量检索      ", plain)
    if cutoffs:
        curve = hit_curve(plain["ranks"], cutoffs)
        report["k_sweep"] = curve
        print(
            "Hit@K 曲线      : "
            + " ".join(f"Hit@{c}={curve[str(c)]:.1%}" for c in cutoffs)
            + f"（同一份排序，k={eval_k}）"
        )

    if args.compare:
        enhanced = evaluate(
            retriever, settings, k=args.k, rewrite=True, rerank=True, verbose=args.verbose
        )
        report["rewrite_rerank"] = enhanced
        show("改写 + 重排     ", enhanced)
        plain_at_k = hit_at(plain["ranks"], args.k)
        print(
            f"差值：Hit@1 {enhanced['hit@1'] - plain['hit@1']:+.1%}，"
            f"Hit@{args.k} {enhanced[f'hit@{args.k}'] - plain_at_k:+.1%}，"
            f"MRR {enhanced['mrr'] - plain['mrr']:+.3f}"
        )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {out}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
