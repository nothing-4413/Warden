"""通知层：控制台 / 文件两种送达方式 + 按配置选择。"""

from __future__ import annotations

import re

from app.config import Settings
from app.notify import ConsoleNotifier, FileNotifier, get_notifier


def test_console_notifier_prints(capsys) -> None:
    ConsoleNotifier().send("周报", "本周做了三件事")
    out = capsys.readouterr().out
    assert "===== 周报 =====" in out
    assert "本周做了三件事" in out


def test_get_notifier_selects_by_kind(tmp_path) -> None:
    file_notifier = get_notifier(Settings(notify_kind="file", report_dir=str(tmp_path / "r")))
    assert isinstance(file_notifier, FileNotifier)
    assert isinstance(get_notifier(Settings(notify_kind="console")), ConsoleNotifier)
    # 大小写不敏感；未知取值回落到控制台（不静默丢报告）
    assert isinstance(
        get_notifier(Settings(notify_kind="FILE", report_dir=str(tmp_path))), FileNotifier
    )
    assert isinstance(get_notifier(Settings(notify_kind="carrier-pigeon")), ConsoleNotifier)


def test_file_notifier_writes_markdown_and_creates_dir(tmp_path, capsys) -> None:
    report_dir = tmp_path / "deep" / "reports"  # 目录不存在时自己建
    FileNotifier(str(report_dir)).send("Warden 周报: 09/10!", "## 进展\n- 写完评测")

    files = list(report_dir.glob("*.md"))
    assert len(files) == 1
    path = files[0]
    assert re.fullmatch(r"warden_周报_09_10_\d{8}_\d{6}\.md", path.name)
    assert path.read_text(encoding="utf-8").startswith("# Warden 周报: 09/10!\n\n## 进展")
    assert "[file-notifier] wrote" in capsys.readouterr().out


def test_file_notifier_truncates_long_slug(tmp_path) -> None:
    report_dir = tmp_path / "r"
    FileNotifier(str(report_dir)).send("标题" * 80, "内容")

    name = next(report_dir.glob("*.md")).name
    assert len(name.split("_2")[0]) <= 60  # slug 截到 60 字符，剩下的交给时间戳


def test_file_notifier_same_second_send_overwrites(tmp_path) -> None:
    """时间戳只到秒：同一秒内同名标题的第二次送达会覆盖第一个文件。

    这是有意为之的取舍（日报不会在同一秒发两次），这里把它固化下来，
    免得以后有人以为是丢了文件。
    """
    report_dir = tmp_path / "r"
    notifier = FileNotifier(str(report_dir))
    notifier.send("日报", "第一次")
    notifier.send("日报", "第二次")

    files = list(report_dir.glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").endswith("第二次\n")
