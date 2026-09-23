"""
路径解析与安全防护工具 — TDD §4.4

职责：
1. 虚拟路径清洗 — 消除路径遍历攻击（.. / ~ / 绝对路径注入）
2. 上传目录处理 — 解析上传会话目录路径
3. 嵌套防护 — 防止过深目录嵌套导致文件系统问题
4. 越界校验 — 确保解析后路径仍在允许的根目录内

使用：
    from utils.path_utils import sanitize_path, resolve_session_dir, validate_within_root
"""

import os
import re
from pathlib import Path

from utils.logger_utils import get_logger

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────
MAX_DEPTH = 10  # 目录嵌套最大深度

# 项目根目录（本文件向上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 运行时目录（相对项目根）
OUTPUT_DIR = PROJECT_ROOT / "output"
UPLOAD_DIR = PROJECT_ROOT / "upload"


# ── 1. 虚拟路径清洗 ─────────────────────────────────────

def sanitize_path(virtual_path: str) -> str:
    """
    清洗用户提供的虚拟路径，消除遍历攻击向量。

    处理项：
    - 去除前导 ~ （防止家目录展开）
    - 去除盘符前缀（C:/ D:\\ 等，防止绝对路径注入）
    - 去除前导 / 或 \\ （防止绝对路径注入、UNC 路径）
    - 替换连续分隔符为单个
    - 剔除 .. 路段（防止目录遍历）
    - 改名 Windows 保留设备名（CON / PRN / AUX / NUL / COM* / LPT*）
    - 去除首尾空白

    为什么必须单独去盘符：Python 的 pathlib 在右侧为绝对路径时会**整体替换**
    左侧的拼接结果，`Path("out/session_x") / "C:/Windows"` 得到的是
    `C:/Windows`，`..` 过滤完全帮不上忙。这是本项目实际踩到过的逃逸路径。

    Args:
        virtual_path: 用户输入的原始路径字符串。

    Returns:
        清洗后的安全相对路径字符串。如果清洗后为空则返回 "."。

    Raises:
        ValueError: 路径清洗后为空或仅包含无效字符。
    """
    if not virtual_path or not virtual_path.strip():
        raise ValueError("路径不能为空")

    p = virtual_path.strip()

    # 去除前导 ~
    p = p.lstrip("~")

    # 去除盘符前缀：C:  C:/  C:\  D:  ...
    p = re.sub(r"^[A-Za-z]:", "", p)

    # 去除前导分隔符（绝对路径注入、UNC 路径）
    p = p.lstrip("/\\").strip()

    # 替换连续分隔符
    p = re.sub(r"[/\\]+", "/", p)

    # 按分隔符拆分，剔除 .. 和空段
    segments = []
    for seg in p.split("/"):
        if seg == "..":
            logger.warning("路径遍历攻击已拦截：剔除 '..' 段")
            continue
        if seg == "." or not seg:
            continue
        segments.append(seg)

    # Windows 保留设备名：改名而非剔除。
    # 剔除会让 "CON.md" 这类输入退化成目录本身；且 Windows 对带扩展名的
    # 保留名（CON.md）同样按设备处理，直接写入会失败或落到控制台。
    _win_reserved = re.compile(
        r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?=\.|$)", re.IGNORECASE
    )
    segments = [
        _win_reserved.sub("_", s) if _win_reserved.match(s) else s
        for s in segments
    ]

    if not segments:
        return "."

    return "/".join(segments)


# ── 2. 上传目录处理 ─────────────────────────────────────

def resolve_upload_dir(thread_id: str) -> Path:
    """
    解析指定会话的上传目录，若不存在则自动创建。

    Args:
        thread_id: 会话标识。

    Returns:
        上传目录的绝对 Path。
    """
    # 先清洗 thread_id 防止注入
    safe_id = sanitize_path(thread_id)
    upload_path = UPLOAD_DIR / f"session_{safe_id}"
    upload_path.mkdir(parents=True, exist_ok=True)
    logger.debug(f"上传目录已就绪：{upload_path}")
    return upload_path


def resolve_output_dir(thread_id: str) -> Path:
    """
    解析指定会话的工作输出目录，若不存在则自动创建。

    Args:
        thread_id: 会话标识。

    Returns:
        输出目录的绝对 Path。
    """
    safe_id = sanitize_path(thread_id)
    output_path = OUTPUT_DIR / f"session_{safe_id}"
    output_path.mkdir(parents=True, exist_ok=True)
    logger.debug(f"输出目录已就绪：{output_path}")
    return output_path


# ── 3. 嵌套防护 ────────────────────────────────────────

def check_nesting_depth(path: Path, max_depth: int = MAX_DEPTH) -> Path:
    """
    检查路径嵌套深度是否超过阈值。

    Args:
        path: 待检查的路径。
        max_depth: 允许的最大嵌套层级（默认 10）。

    Returns:
        原路径（若通过检查）。

    Raises:
        ValueError: 嵌套深度超过阈值。
    """
    resolved = path.resolve()
    depth = len(resolved.parts)
    if depth > max_depth:
        raise ValueError(
            f"路径嵌套深度 {depth} 超过上限 {max_depth}：{resolved}"
        )
    return path


# ── 4. 越界校验 ────────────────────────────────────────

def validate_within_root(path: Path, root: Path) -> Path:
    """
    校验目标路径是否位于指定的根目录内，防止路径穿越越界。

    通过 resolve() 消除符号链接后再比较，确保无绕过。

    Args:
        path: 待校验的目标路径（绝对或相对）。
        root: 允许的根目录（绝对路径）。

    Returns:
        解析后的绝对路径（若通过校验）。

    Raises:
        ValueError: 路径越界。
    """
    resolved_target = path.resolve()
    resolved_root = root.resolve()

    # 检查 resolved_target 是否以 resolved_root 为前缀
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"路径越界：{resolved_target} 不在允许目录 {resolved_root} 内"
        )

    return resolved_target


def safe_resolve(virtual_path: str, root: Path) -> Path:
    """
    一步到位的安全路径解析：清洗 → 拼接 → 嵌套检查 → 越界校验。

    Args:
        virtual_path: 用户输入的虚拟路径。
        root: 允许的根目录。

    Returns:
        解析后且通过校验的绝对路径。

    Raises:
        ValueError: 路径为空、嵌套过深或越界。
    """
    clean = sanitize_path(virtual_path)

    # 嵌套深度按「相对路径」计算，而不是绝对路径。
    # 用绝对路径数层级会把 C:\Users\...\探渊项目\output\session_x 这 8 层
    # 也算进去，导致阈值随项目所在目录深度漂移、合法路径被误拒。
    depth = len([s for s in clean.split("/") if s not in ("", ".")])
    if depth > MAX_DEPTH:
        raise ValueError(f"路径嵌套深度 {depth} 超过上限 {MAX_DEPTH}：{virtual_path!r}")

    full = root / clean
    return validate_within_root(full, root)


# ── __main__ 安全回归测试 ────────────────────────────────
if __name__ == "__main__":
    import tempfile

    # 用临时目录做 root，避免污染 output/upload
    with tempfile.TemporaryDirectory() as _tmp:
        root = Path(_tmp) / "session_test"
        root.mkdir()

        print("=== 攻击向量：必须全部落在 root 内 ===")
        attacks = [
            "C:\\Windows\\System32\\drivers\\etc\\hosts",
            "C:/Windows/System32",
            "D:\\evil.md",
            "Z:/",
            "../../../../Windows/win.ini",
            "..\\..\\..\\Windows\\win.ini",
            "....//....//etc/passwd",
            "//server/share/evil.md",
            "\\\\server\\share\\evil.md",
            "~/../../etc/passwd",
            "/etc/passwd",
            "\\Windows\\win.ini",
            "a/../../../../../../tmp/x.md",
            "C:evil.md",
        ]
        escaped = 0
        for vec in attacks:
            try:
                resolved = safe_resolve(vec, root)
                inside = root.resolve() in resolved.parents or resolved == root.resolve()
                mark = "OK " if inside else "ESCAPED"
                if not inside:
                    escaped += 1
                # 只打印相对位置，避免暴露本机路径
                rel = str(resolved).replace(str(root.resolve()), "<ROOT>")
                print(f"  {mark}  {vec!r:46} -> {rel}")
            except ValueError as e:
                print(f"  DENY  {vec!r:46} -> 已拒绝：{e}")

        print(f"\n  逃逸数：{escaped}（必须为 0）")

        print("\n=== 保留设备名：应被改名而非逃逸 ===")
        for name in ["CON.md", "PRN.txt", "AUX.md", "NUL.md", "COM1.md", "LPT1.md"]:
            try:
                resolved = safe_resolve(name, root)
                print(f"  OK  {name!r:12} -> {resolved.name}")
            except ValueError as e:
                print(f"  DENY {name!r:12} -> {e}")

        print("\n=== 深度限制 ===")
        for depth in (5, 11, 20):
            vec = "/".join(f"d{i}" for i in range(depth))
            try:
                safe_resolve(vec, root)
                print(f"  放行  深度 {depth}")
            except ValueError as e:
                print(f"  拒绝  深度 {depth}：{e}")

        print("\n=== 正常相对路径：应放行 ===")
        for ok_path in ["report.md", "sub/report.md", "a/b/c/d.md"]:
            try:
                resolved = safe_resolve(ok_path, root)
                print(f"  OK  {ok_path!r:20} -> {resolved.name}")
            except ValueError as e:
                print(f"  BAD {ok_path!r:20} -> 不该被拒绝：{e}")
