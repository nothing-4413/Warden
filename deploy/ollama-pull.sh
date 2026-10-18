#!/bin/sh
# 一次性初始化脚本：把 .env 指定的对话模型与嵌入模型拉进 Ollama 的模型卷。
# 由 deploy/docker-compose.yml 的 ollama-pull 服务挂载执行；拉完即退出（Exited 0 是正常的）。
# 单独调试：docker run --rm --network <compose 网络> -e OLLAMA_HOST=http://ollama:11434 \
#            -v "$PWD/ollama-pull.sh:/usr/local/bin/ollama-pull.sh:ro" \
#            --entrypoint /bin/sh ollama/ollama /usr/local/bin/ollama-pull.sh
set -eu

# 优先用显式列表，否则从 app 的同一份 .env 派生（与 app 实际使用的模型保持一致）
models="${WARDEN_OLLAMA_MODELS:-}"
if [ -z "$models" ]; then
    models="${WARDEN_LLM_MODEL:-qwen2.5:7b},${WARDEN_EMBEDDING_MODEL:-nomic-embed-text}"
fi

IFS=','
for model in $models; do
    # 容忍 "a, b" 这种带空格写法
    model=$(printf '%s' "$model" | tr -d '[:space:]')
    [ -n "$model" ] || continue

    echo "[ollama-pull] pulling $model"
    attempt=1
    until ollama pull "$model"; do
        attempt=$((attempt + 1))
        if [ "$attempt" -gt 3 ]; then
            echo "[ollama-pull] failed to pull $model (already retried 3 times)" >&2
            exit 1
        fi
        echo "[ollama-pull] retry $attempt/3 for $model"
        sleep 5
    done
done

echo "[ollama-pull] done"
