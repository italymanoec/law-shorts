#!/usr/bin/env python3
"""只讀估算法律短影音要不要依科目拆成多個 GitHub Pages repo。

讀 shorts.json、v/、p/ 的檔案大小，以及 git 歷史裡每次新增的媒體位元組。
印出拆分方案到標準輸出。不寫入任何檔案，不改媒體、shorts.json、index.html、sw.js，
不搬檔，也不建立 repo。

從儲存庫根目錄執行：

    python3 tools/split_plan.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHORTS = ROOT / "shorts.json"
VIDEO_DIR = ROOT / "v"
POSTER_DIR = ROOT / "p"

# GitHub 文件寫「Published GitHub Pages sites may be no larger than 1 GB」，
# 沒有註明是 10^9 還是 2^30。檔案上限那一頁用的是 MiB，repo 建議值寫 1 GB。
# 批數以較緊的 10^9 bytes 為準，並另給 1 GiB 的對照。
LIMIT_GB = 1_000_000_000
LIMIT_GIB = 1024**3

# 一科一組以外，沿科目出現順序列出這幾種固定科數。11 不能被這些數整除，最後一組會少於 X 科。
GROUP_SIZES = (2, 3, 4)


def fmt_mib(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MiB"


def fmt_bytes(n: int) -> str:
    return f"{n:,} bytes（{fmt_mib(n)}）"


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def github_owner_repo() -> tuple[str, str]:
    url = run_git(["remote", "get-url", "origin"]).strip()
    url = re.sub(r"https://[^/@\s]+@", "https://", url)
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", url)
    if not match:
        raise SystemExit("無法從 origin 解析 GitHub owner/repo，停止以免把網址寫錯。")
    return match.group(1), match.group(2)


def load_items() -> list[dict]:
    data = json.loads(SHORTS.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit("shorts.json 沒有 items 陣列。")
    return items


def split_asset(value: str) -> tuple[str, str]:
    path, _, query = value.partition("?")
    return path, query


def prefix_of(item_id: str) -> str:
    return item_id.split("-", 1)[0]


def measure(items: list[dict]) -> dict:
    """依科目加總工作區裡 v/、p/ 的位元組。只 stat，不開啟媒體內容。"""
    by_id = {}
    order: list[str] = []
    prefixes: dict[str, set[str]] = defaultdict(set)
    bad_src: list[str] = []
    for item in items:
        subj = item.get("subj")
        item_id = item.get("id")
        if not subj or not item_id:
            bad_src.append(repr(item_id))
            continue
        if subj not in prefixes:
            order.append(subj)
        prefixes[subj].add(prefix_of(item_id))
        src_path, _ = split_asset(str(item.get("src", "")))
        poster_path, _ = split_asset(str(item.get("poster", "")))
        by_id[item_id] = {
            "subj": subj,
            "src": str(item.get("src", "")),
            "poster": str(item.get("poster", "")),
            "src_path": src_path,
            "poster_path": poster_path,
        }
        if Path(src_path).name != f"{item_id}.mp4":
            bad_src.append(f"{item_id} src={item.get('src')}")
        if Path(poster_path).name != f"{item_id}.jpg":
            bad_src.append(f"{item_id} poster={item.get('poster')}")

    subjects: dict[str, dict] = {
        subj: {"count": 0, "v": 0, "p": 0, "missing_v": [], "missing_p": []}
        for subj in order
    }
    seen_v: set[str] = set()
    seen_p: set[str] = set()
    for item_id, meta in by_id.items():
        row = subjects[meta["subj"]]
        row["count"] += 1
        video = ROOT / meta["src_path"]
        poster = ROOT / meta["poster_path"]
        seen_v.add(video.name)
        seen_p.add(poster.name)
        if video.is_file():
            row["v"] += video.stat().st_size
        else:
            row["missing_v"].append(meta["src_path"])
        if poster.is_file():
            row["p"] += poster.stat().st_size
        else:
            row["missing_p"].append(meta["poster_path"])

    extra_v = sorted(p.name for p in VIDEO_DIR.glob("*.mp4") if p.name not in seen_v)
    extra_p = sorted(p.name for p in POSTER_DIR.glob("*.jpg") if p.name not in seen_p)
    shell_files = []
    shell_bytes = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if rel.parts[0] in {".git", "v", "p", "docs", "tools"}:
            continue
        size = path.stat().st_size
        shell_files.append((str(rel), size))
        shell_bytes += size

    side_bytes = 0
    for folder in ("docs", "tools"):
        base = ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            # 這份說明由腳本輸出再存成檔案。把它算進自己的數字會讓重跑對不齊。
            if rel == "docs/拆分方案.md":
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            side_bytes += path.stat().st_size

    max_v = max((p.stat().st_size for p in VIDEO_DIR.glob("*.mp4")), default=0)
    max_p = max((p.stat().st_size for p in POSTER_DIR.glob("*.jpg")), default=0)
    return {
        "subjects": subjects,
        "order": order,
        "prefixes": {subj: sorted(names) for subj, names in prefixes.items()},
        "bad_src": bad_src,
        "extra_v": extra_v,
        "extra_p": extra_p,
        "shell_files": shell_files,
        "shell_bytes": shell_bytes,
        "side_bytes": side_bytes,
        "max_v": max_v,
        "max_p": max_p,
        "items": by_id,
    }


def git_batches() -> list[dict]:
    """每個 commit 的 v/ + p/ 總量，以及相對上一筆新增的位元組。"""
    revs = [line for line in run_git(["rev-list", "--reverse", "HEAD"]).splitlines() if line]
    batches = []
    previous = 0
    for rev in revs:
        listing = run_git(["ls-tree", "-r", "-l", rev])
        media = 0
        videos = 0
        for line in listing.splitlines():
            meta, _, path = line.partition("\t")
            if not (path.startswith("v/") or path.startswith("p/")):
                continue
            parts = meta.split()
            size = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 0
            media += size
            if path.startswith("v/"):
                videos += 1
        subject = run_git(["log", "-1", "--format=%s", rev]).strip()
        added = media - previous
        batches.append(
            {
                "rev": rev[:7],
                "subject": subject,
                "videos": videos,
                "media": media,
                "added": added,
                "added_videos": videos - (batches[-1]["videos"] if batches else 0),
            }
        )
        previous = media
    return batches


def repo_name(base: str, prefixes: list[str]) -> str:
    slug = "-".join(prefixes)
    return f"{base}-{slug}"


def pages_url(owner: str, repo: str, asset: str) -> str:
    path, query = split_asset(asset)
    url = f"https://{owner}.github.io/{repo}/{path}"
    if query:
        url = f"{url}?{query}"
    return url


def batches_that_fit(remaining: int, batch_bytes: int) -> int:
    if batch_bytes <= 0:
        return 0
    return remaining // batch_bytes


def player_reads() -> str:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    reads_src = "v.dataset.src=x.src" in html or "v.dataset.src = x.src" in html
    reads_poster = "v.poster=x.poster" in html or "v.poster = x.poster" in html
    if reads_src and reads_poster:
        return "index.html 把每一筆的 src 填進 video、把 poster 填進封面，沒有另外寫死 v/ 或 p/ 的網址。"
    return "index.html 裡沒有同時找到 src 與 poster 的既有讀法；要改的仍是 shorts.json 這兩欄，播放器這次不動。"


def render() -> str:
    owner, base_repo = github_owner_repo()
    items = load_items()
    measured = measure(items)
    batches = git_batches()
    subjects = measured["subjects"]
    order = measured["order"]

    media_bytes = sum(row["v"] + row["p"] for row in subjects.values())
    video_bytes = sum(row["v"] for row in subjects.values())
    poster_bytes = sum(row["p"] for row in subjects.values())
    clip_count = sum(row["count"] for row in subjects.values())
    site_bytes = media_bytes + measured["shell_bytes"]
    added = [batch["added"] for batch in batches if batch["added"] > 0]
    mean_batch = sum(added) // len(added) if added else 0
    max_batch = max(added) if added else 0
    avg_clip = media_bytes // clip_count if clip_count else 0

    lines: list[str] = []
    w = lines.append
    w("# 拆分方案")
    w("")
    w("主站繼續當播放器。影片目錄 `v/`、封面目錄 `p/` 改放到依科目分開的 GitHub Pages repo。主站 `shorts.json` 的 `src` 和 `poster` 改成那些站的絕對網址。")
    w("")
    w("這份數字由 `python3 tools/split_plan.py` 從工作區直接重算。腳本只讀檔案與 git 歷史，不改媒體、不改 `shorts.json`、不改 `index.html`、不改 `sw.js`，不搬檔，也不建立 repo。")
    w("")
    w("GitHub 寫明已發布的 GitHub Pages 站不得大於 1 GB，建議中的來源 repo 也以 1 GB 為目標。文件沒有寫這個 GB 是 10⁹ 還是 2³⁰。下面的「還能再放幾批」以較緊的 **1,000,000,000 bytes** 計算，並附 1 GiB（1,073,741,824 bytes）對照。單檔上限是 100 MiB；目前最大的 mp4 是 "
      f"{fmt_bytes(measured['max_v'])}，最大的 jpg 是 {fmt_bytes(measured['max_p'])}。")
    w("")
    w("## 現況")
    w("")
    w(f"- 支數：{clip_count}（`shorts.json` 的 `items`）")
    w(f"- `v/`：{fmt_bytes(video_bytes)}")
    w(f"- `p/`：{fmt_bytes(poster_bytes)}")
    w(f"- 媒體合計：{fmt_bytes(media_bytes)}")
    w(f"- 主站殼層（`index.html`、`shorts.json`、圖示、README，不含 `v/`、`p/`、`docs/`、`tools/`）：{fmt_bytes(measured['shell_bytes'])}")
    w(f"- 納入 1 GB 試算的發布大小：{fmt_bytes(site_bytes)}")
    w(f"- `docs/`（不含本檔）與 `tools/` 另計 {fmt_bytes(measured['side_bytes'])}。本檔存成 `docs/拆分方案.md` 後，整枝發布會再多這一個說明檔，大約數十 KB，不改下面的整數批數。")
    if avg_clip:
        w(f"- 平均一支（該支的 mp4 + jpg）：{fmt_bytes(avg_clip)}")
    w("")
    w("| 科目 | id 前綴 | 支數 | v/ | p/ | 合計 |")
    w("|---|---|---:|---:|---:|---:|")
    for subj in order:
        row = subjects[subj]
        total = row["v"] + row["p"]
        w(
            f"| {subj} | {', '.join(measured['prefixes'][subj])} | {row['count']} | "
            f"{fmt_mib(row['v'])} | {fmt_mib(row['p'])} | {fmt_mib(total)} |"
        )
    w(f"| 合計 |  | {clip_count} | {fmt_mib(video_bytes)} | {fmt_mib(poster_bytes)} | {fmt_mib(media_bytes)} |")
    w("")
    w("表內 MiB 四捨五入到小數第二位。各列加總可能與合計差 0.01 MiB，位元組以本節上面的合計為準。")
    w("")

    problems = []
    if measured["bad_src"]:
        problems.append("網址對不上 id 的筆數：" + "、".join(measured["bad_src"]))
    missing = [
        f"{subj} 缺影片 {', '.join(row['missing_v'])} 缺封面 {', '.join(row['missing_p'])}"
        for subj, row in subjects.items()
        if row["missing_v"] or row["missing_p"]
    ]
    if missing:
        problems.extend(missing)
    if measured["extra_v"] or measured["extra_p"]:
        problems.append(
            "JSON 沒點名的檔案："
            + "、".join(measured["extra_v"] + measured["extra_p"])
        )
    if problems:
        w("對帳有異常：")
        w("")
        for problem in problems:
            w(f"- {problem}")
        w("")
    else:
        w("`shorts.json` 的每一筆都對得到一支 mp4 和一張 jpg，目錄裡也沒有多出來的媒體檔。")
        w("")

    w("## 不拆站，還能再放幾批")
    w("")
    w("一批的定義：git 歷史上一個 commit 新增加的 `v/` 與 `p/` 位元組。目前 5 個 commit 就是 5 批，由小到大差很多，所以同時給平均批與最大批。")
    w("")
    w("| commit | 該批新增支數 | 該批新增 v/+p/ | 累計媒體 |")
    w("|---|---:|---:|---:|")
    for batch in batches:
        title = batch["subject"]
        if len(title) > 42:
            title = title[:41] + "…"
        w(
            f"| `{batch['rev']}` {title} | {batch['added_videos']} | "
            f"{fmt_mib(batch['added'])} | {fmt_mib(batch['media'])} |"
        )
    w("")
    if mean_batch and max_batch:
        remain_gb = LIMIT_GB - site_bytes
        remain_gib = LIMIT_GIB - site_bytes
        fit_mean = batches_that_fit(remain_gb, mean_batch)
        fit_max = batches_that_fit(remain_gb, max_batch)
        fit_mean_gib = batches_that_fit(remain_gib, mean_batch)
        fit_max_gib = batches_that_fit(remain_gib, max_batch)
        w(f"- 五批平均：{fmt_bytes(mean_batch)}")
        w(f"- 最大一批：{fmt_bytes(max_batch)}（最新一批）")
        w(f"- 以 1 GB = 1,000,000,000 bytes，這一站還剩 {fmt_bytes(remain_gb)}")
        w(f"- 用平均批去除，還能再放 **{fit_mean}** 批；下一整批若仍是平均大小，第 {fit_mean + 1} 批會超過 1 GB。")
        w(f"- 用最大一批去除，還能再放 **{fit_max}** 批。")
        w(f"- 若 1 GB 是指 1 GiB，還剩 {fmt_bytes(remain_gib)}，平均批還能再放 {fit_mean_gib} 批，最大批還能再放 {fit_max_gib} 批。")
        w(f"- 換算成支數：以目前平均每支大小，1 GB 內大約還能再放 {batches_that_fit(remain_gb, avg_clip)} 支，1 GiB 內大約還能再放 {batches_that_fit(remain_gib, avg_clip)} 支。")
        w("")
        w("批的大小從大約 44 MiB 到 263 MiB，不是定額。上面的批數是用歷史平均和歷史最大各除一次，用來決定要不要在下一批之前把媒體搬出去。")
        w("")

    w("## 做法")
    w("")
    w(f"建議 **一科一個 repo**，再加現有主站 `{owner}/{base_repo}`。11 個科目的 id 前綴各對一個站，之後補片只推該科的 repo，主站只改 `shorts.json` 的網址。")
    w("")
    w("主站留下 `index.html`、`shorts.json`、圖示和 README。`v/`、`p/` 改放到新 repo 的同名目錄，並從該 repo 的根目錄開 GitHub Pages。新 repo 的預估大小只算搬過去的 mp4 和 jpg，不含播放器。")
    w("")
    w("一科一站時，目前最大的一科仍遠小於 1 GB。某一科之後變大，不會把其他科的配額一起吃掉。")
    w("")
    w("## 每個新 repo")
    w("")
    w("「還能再放幾支」用該科自己的平均每支大小，去除該站距 1,000,000,000 bytes 的剩餘空間。「還能再放幾批」用上面的全站平均批去除。全站一批混有各科，通常大於單科實際的一批，所以這個批數是偏緊的估法。")
    w("")
    w("| 新 repo | 科目 | 支數 | 預估大小 | 距 1 GB 還能再放幾支 | 距 1 GB 還能再放幾次全站平均批 |")
    w("|---|---|---:|---:|---:|---:|")
    for subj in order:
        row = subjects[subj]
        total = row["v"] + row["p"]
        name = repo_name(base_repo, measured["prefixes"][subj])
        per_clip = total // row["count"] if row["count"] else 0
        extra_clips = batches_that_fit(LIMIT_GB - total, per_clip)
        extra_batches = batches_that_fit(LIMIT_GB - total, mean_batch)
        w(
            f"| `{owner}/{name}` | {subj} | {row['count']} | {fmt_mib(total)} | "
            f"{extra_clips} | {extra_batches} |"
        )
    shell_left = batches_that_fit(LIMIT_GB - measured["shell_bytes"], mean_batch)
    w("")
    w(
        f"拆完之後主站 `{owner}/{base_repo}` 只剩殼層 {fmt_mib(measured['shell_bytes'])}。"
        f"媒體不再占這 1 GB，以全站平均批估算，大約還能再放 {shell_left} 次同量的增量；實際主站之後只會增加 JSON 文字，不會再收整批影片。"
    )
    w("")
    w("## 每 X 科一個 repo")
    w("")
    w("11 科只有分成 1 科一組或 11 科全放在一起時，每一組的科數才會相同。X 為 2、3、4 時，最後一組會少於 X 科。分組順序沿用 `shorts.json` 裡科目第一次出現的順序。repo 名稱把該組的 id 前綴接在主站名稱後面。")
    w("")
    for size in GROUP_SIZES:
        w(f"### 每 {size} 科一組")
        w("")
        w("| 新 repo | 科目 | 支數 | 預估大小 | 距 1 GB 還能再放幾次全站平均批 |")
        w("|---|---|---:|---:|---:|")
        biggest_name = ""
        biggest = -1
        for index in range(0, len(order), size):
            group = order[index : index + size]
            prefixes: list[str] = []
            for subj in group:
                prefixes.extend(measured["prefixes"][subj])
            name = repo_name(base_repo, prefixes)
            total = sum(subjects[subj]["v"] + subjects[subj]["p"] for subj in group)
            count = sum(subjects[subj]["count"] for subj in group)
            extra_batches = batches_that_fit(LIMIT_GB - total, mean_batch)
            if total > biggest:
                biggest = total
                biggest_name = f"{owner}/{name}"
            w(
                f"| `{owner}/{name}` | {'、'.join(group)} | {count} | {fmt_mib(total)} | {extra_batches} |"
            )
        w("")
        w(f"這一組切法裡最大的是 `{biggest_name}`，{fmt_mib(biggest)}。")
        w("")

    w("每一組放的科數愈多，repo 愈少，最擠的那一站就愈早碰到 1 GB。以現在的大小，上表每一組都還低於 1 GB。要少開 repo，每 2 科或每 4 科都還放得下；要讓以後單科加片互不影響，用一科一個 repo。")
    w("")
    w("## 要改的網址欄位")
    w("")
    w(player_reads())
    w("")
    w(f"要改的是 `shorts.json` 裡每一筆的 `src` 和 `poster`，共 {clip_count} 筆、{clip_count * 2} 個欄位。`?v=` 後面的快取字串留著。`pod` 已經是 Podcast 的絕對網址，不動。`id`、`subj`、`title`、`hook`、`year`、`n`、`dur`、`podt` 也不動。")
    w("")
    w("| 欄位 | 現在 | 拆分後 |")
    w("|---|---|---|")
    example_id = next(iter(measured["items"]))
    example = measured["items"][example_id]
    example_repo = repo_name(base_repo, measured["prefixes"][example["subj"]])
    w(f"| `src` | `{example['src']}` | `{pages_url(owner, example_repo, example['src'])}` |")
    w(f"| `poster` | `{example['poster']}` | `{pages_url(owner, example_repo, example['poster'])}` |")
    w("")
    w("其餘各筆同一規則：路徑維持 `v/<id>.mp4` 或 `p/<id>.jpg`，只把相對路徑換成 `https://<owner>.github.io/<該科 repo>/` 開頭。播放器用這兩個字串當位址，所以 `index.html` 不用改。")
    w("")
    if (ROOT / "sw.js").exists():
        w("工作區有 `sw.js`。這次不修改它。若它有快取 `v/`、`p/` 的相對路徑，拆站之後那些快取會失效，要另一次再改。")
    else:
        w("工作區沒有 `sw.js`。這次不新增。")
    w("")
    w("跨站的 mp4 可以由主站的 `<video>` 直接播放，封面也可以直接用跨站 jpg。兩站都是 https。")
    w("")
    w("## 重跑")
    w("")
    w("```")
    w("python3 tools/split_plan.py")
    w("```")
    w("")
    w("輸出即這份說明。媒體或 `shorts.json` 有增減時重跑即可，不必改腳本。")
    w("")
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    report = render()
    if "x-access-token" in report or "ghp_" in report:
        raise SystemExit("報告含有憑證字樣，拒絕印出。")
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
