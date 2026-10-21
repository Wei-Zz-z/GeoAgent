"""读取上传的前后时相 / 永农 / 红线矢量，并做编码、几何与坐标系兜底。

实测源数据有这几个坑，这里统一兜住：

1. ``.prj`` 无法自动识别 EPSG（``to_epsg()`` 为空）——按 CGCS2000 3 度带 40 带
   绑定到 EPSG:4528；
2. 前后时相存在未闭合环，直接读会抛 GEOS 异常——放开 OGR 容错并逐要素修复；
3. 属性表 ``.cpg`` 声明 UTF-8 但内容实际是 GBK——按编码候选逐个尝试；
4. 只传了 ``.shp``（漏选 ``.shx``）：让 GDAL 自动重建空间索引；若连 ``.dbf``/``.prj``
   都缺，则明确告诉用户缺哪些文件，而不是抛 GDAL 的原文错误。
"""

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from zipfile import ZipFile

from .spec import (
    DEFAULT_SOURCE_EPSG,
    HSX_OPTIONAL_FIELDS,
    LAYERS,
    QSX_OPTIONAL_FIELDS,
    TEXT_WIDTHS,
    layer_label,
)

# 单个图层允许上传的总字节数（全量四个图层合计约 117MB，这里留足余量）。
MAX_LAYER_BYTES = 400 * 1024 * 1024

# Shapefile 属性表编码候选：``.cpg`` 声明不可信，按顺序尝试。
_ENCODINGS = ("UTF-8", "GBK", "GB18030")

# GDAL 容错配置：
# - 未闭合环照单接收，入库前统一 ST_MakeValid；
# - ``.shx`` 缺失时自动重建（只选了 .shp/.dbf 也能读）。
_GDAL_CONFIG = {
    "OGR_GEOMETRY_ACCEPT_UNCLOSED_RING": "YES",
    "SHAPE_RESTORE_SHX": "YES",
}

# Shapefile 配套文件：缺 .dbf 读不到属性，缺 .prj 无法确定坐标系，.shx 可自动重建。
_REQUIRED_SIDECARS = ((".dbf", "属性表"), (".prj", "坐标系"))


@dataclass
class LayerInfo:
    """一个输入图层的读取信息，用于结果摘要与排查。"""

    role: str
    label: str
    rows: int
    source: str
    encoding: str
    epsg: int
    fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "rows": self.rows,
            "source": self.source,
            "encoding": self.encoding,
            "epsg": self.epsg,
            "fields": self.fields,
            "warnings": self.warnings,
        }


@dataclass
class LayerData:
    """读取结果：标准化后的属性表 + 读取信息。"""

    role: str
    frame: Any
    info: LayerInfo

    @property
    def columns(self) -> list[str]:
        return [c for c in self.frame.columns if c != self.frame.geometry.name]


def _configure_gdal() -> None:
    try:
        import pyogrio

        pyogrio.set_gdal_config_options(_GDAL_CONFIG)
    except Exception:  # pragma: no cover - 环境差异时不影响主流程
        pass


def _sidecar_problems(shp: Path) -> list[str]:
    """检查 Shapefile 配套文件；返回缺失项的中文描述（.shx 缺失不算问题）。"""
    problems: list[str] = []
    for suffix, why in _REQUIRED_SIDECARS:
        if not shp.with_suffix(suffix).exists():
            problems.append(f"{shp.stem}{suffix}（缺少{why}）")
    return problems


# 图层角色 → 文件/成员名关键词：一份 ZIP 里含多套 Shapefile 时，按这些关键词挑出对应该图层的那一套。
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hsx": ("hsx", "dlbhjctb", "变化监测"),
    "qsx": ("qsx", "bgdltb", "变更地类"),
    "yjn": ("jbn", "yjn", "永农", "基本农田"),
    "hx": ("hhx", "stbh", "红线"),
}


def _match_member(names: Sequence[str], role: str) -> tuple[Optional[str], list[str]]:
    """在多个 Shapefile 里挑出该图层对应的那个；挑不出时返回候选清单。"""
    keywords = _ROLE_KEYWORDS.get(role, ())
    scored: list[tuple[int, str]] = []
    for name in names:
        lowered = Path(name).name.lower()
        score = sum(1 for keyword in keywords if keyword.lower() in lowered)
        if score:
            scored.append((score, name))
    if not scored:
        return None, [Path(n).name for n in names]
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, [Path(n).name for _, n in scored]
    return scored[0][1], []


def _inspect_files(files: Sequence[Path], role: str) -> list[str]:
    """检查本地文件清单是否成套（.shx 缺失可自动重建，不算问题）。"""
    shapes = [p for p in files if p.suffix.lower() == ".shp"]
    if not shapes:
        geojson = [p for p in files if p.suffix.lower() in (".geojson", ".json")]
        if len(geojson) == 1:
            return []
        return ["需要一套 Shapefile（含一个 .shp）、一个 GeoJSON 或一份 ZIP"]
    if len(shapes) > 1:
        chosen, candidates = _match_member([p.name for p in shapes], role)
        if chosen is None:
            return [f"目录里有多个 Shapefile（{'、'.join(candidates)}），无法确定哪一个属于该图层"]
        shapes = [p for p in shapes if p.name == chosen]
    return _sidecar_problems(shapes[0])


def _inspect_archive(archive: Path, role: str) -> list[str]:
    """检查 ZIP 内是否有一套可用的 Shapefile。"""
    try:
        with ZipFile(archive) as handle:
            members = [
                item.filename
                for item in handle.infolist()
                if not item.is_dir() and not item.filename.startswith("__MACOSX")
            ]
    except Exception as exc:
        return [f"ZIP 无法读取（{type(exc).__name__}）"]
    shapes = [name for name in members if name.lower().endswith(".shp")]
    if not shapes:
        if any(name.lower().endswith((".geojson", ".json")) for name in members):
            return []
        return ["ZIP 里没有 .shp 或 GeoJSON"]
    if len(shapes) > 1:
        chosen, candidates = _match_member(shapes, role)
        if chosen is None:
            return [
                f"ZIP 里有多个 Shapefile（{'、'.join(candidates)}），无法确定哪一个属于该图层；"
                "请按图层分别打包 ZIP"
            ]
        shapes = [chosen]
    stem = Path(shapes[0]).stem
    present = {Path(name).stem.lower() + Path(name).suffix.lower() for name in members}
    problems = [
        f"{stem}{suffix}（缺少{why}）"
        for suffix, why in _REQUIRED_SIDECARS
        if (stem.lower() + suffix) not in present
    ]
    return problems


def inspect_layer_files(paths: Sequence[Path], role: str) -> list[str]:
    """检查一个图层收到的文件是否成套，返回问题清单（空表示可以读取）。

    路由层在启动套合任务前调用，用于把"漏选文件"这类问题立刻反馈给用户。
    """
    files = [Path(p) for p in paths if Path(p).is_file()]
    if not files:
        return ["没有收到文件"]
    archives = [p for p in files if p.suffix.lower() == ".zip"]
    if archives:
        if len(archives) > 1:
            return ["每个图层只接收一份 ZIP"]
        return _inspect_archive(archives[0], role)
    return _inspect_files(files, role)


def _extract_archive(archive: Path, workspace: Path) -> list[Path]:
    """把 ZIP 解压到临时工作目录：解压后 GDAL 才能补写 .shx 等文件。"""
    target = workspace / (archive.stem or "layer")
    target.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with ZipFile(archive) as handle:
        for item in handle.infolist():
            if item.is_dir():
                continue
            raw = item.filename.replace("\\", "/")
            if raw.startswith("/") or ".." in Path(raw).parts:
                raise ValueError("ZIP 包含不安全路径。")
            destination = target / Path(raw).name
            with handle.open(item) as source, destination.open("wb") as out:
                shutil.copyfileobj(source, out)
            extracted.append(destination)
    if not extracted:
        raise ValueError("ZIP 里没有文件。")
    return extracted


def _geo_source(paths: Sequence[Path], workspace: Path, role: str) -> tuple[str, str, list[str]]:
    """定位要读取的矢量数据源，返回 (可读路径, 展示名, 警告)。"""
    files = [Path(p) for p in paths if Path(p).is_file()]
    if not files:
        raise ValueError("没有收到有效的矢量文件。")
    archives = [p for p in files if p.suffix.lower() == ".zip"]
    if archives:
        if len(archives) > 1:
            raise ValueError("每个图层只接收一份 ZIP。")
        archive = archives[0]
        problems = _inspect_archive(archive, role)
        if problems:
            raise ValueError(f"{archive.name} 无法作为{layer_label(role)}使用：" + "；".join(problems))
        extracted = _extract_archive(archive, workspace)
        source, shown, warnings = _pick_source(extracted, role)
        return source, f"{archive.name}/{shown}", warnings
    return _pick_source(files, role)


def _pick_source(files: Sequence[Path], role: str) -> tuple[str, str, list[str]]:
    """从文件清单里挑出唯一数据源，并校验 Shapefile 是否成套。"""
    geojson = [p for p in files if p.suffix.lower() in (".geojson", ".json")]
    shapes = [p for p in files if p.suffix.lower() == ".shp"]
    if len(shapes) == 0 and len(geojson) == 1:
        return str(geojson[0]), geojson[0].name, []
    if len(shapes) != 1:
        if len(shapes) > 1:
            chosen, candidates = _match_member([p.name for p in shapes], role)
            if chosen is None:
                raise ValueError(
                    f"这个位置收到多个 Shapefile（{'、'.join(candidates)}），"
                    f"无法确定哪一个属于{layer_label(role)}。请只上传该图层的文件，或按图层分别打包 ZIP。"
                )
            shapes = [p for p in shapes if p.name == chosen]
        else:
            raise ValueError(
                "请上传一套 Shapefile（.shp/.shx/.dbf/.prj/.cpg 一起选中）或打包成 ZIP，"
                "也可以上传单个 GeoJSON。"
            )
    shp = shapes[0]
    problems = _sidecar_problems(shp)
    if problems:
        raise ValueError(
            f"{shp.name} 缺少配套文件：{'、'.join(problems)}。"
            "Shapefile 需要 .shp/.shx/.dbf/.prj/.cpg 整套一起上传，或打包成 ZIP 上传。"
        )
    warnings: list[str] = []
    if not shp.with_suffix(".shx").exists():
        warnings.append(f"{shp.stem}.shx 缺失，已自动重建空间索引。")
    return str(shp), shp.name, warnings

class _EncodingFailure(Exception):
    """属性表编码不匹配（内部信号，外层会换一种编码重试）。"""


def _friendly_read_error(exc: Exception) -> str:
    """把 GDAL 原文错误翻译成用户能照着做的提示。"""
    text = str(exc)
    upper = text.upper()
    if "SHX" in upper:
        return (
            "读取矢量失败：缺少 .shx 空间索引且自动重建未成功。"
            "请把整套 Shapefile（.shp/.shx/.dbf/.prj/.cpg）一起上传，或打包成 ZIP。"
        )
    if "UNABLE TO OPEN" in upper:
        return (
            f"读取矢量失败：{text}。请确认 .shp/.dbf/.prj 是否齐全、"
            "文件是否被其他程序占用。"
        )
    return f"读取矢量失败：{type(exc).__name__}: {text}"


def _read_frame(source: str, encoding: Optional[str]) -> Any:
    """按指定编码读取矢量；编码异常用内部信号抛出，其余错误直接上报。"""
    import geopandas as gpd

    kwargs: dict[str, Any] = {"on_invalid": "fix"}
    if encoding:
        kwargs["encoding"] = encoding
    try:
        return gpd.read_file(source, **kwargs)
    except (UnicodeDecodeError, UnicodeError) as exc:
        raise _EncodingFailure(str(exc)) from exc


def _read_with_encoding_fallback(source: str, is_shp: bool) -> tuple[Any, str]:
    """依次尝试候选编码读取属性表。"""
    if not is_shp:
        return _read_frame(source, None), "UTF-8"
    last_error: Optional[Exception] = None
    for encoding in _ENCODINGS:
        try:
            return _read_frame(source, encoding), encoding
        except _EncodingFailure as exc:
            last_error = exc
            continue
        except Exception as exc:  # 几何、缺文件等错误直接上报
            raise ValueError(_friendly_read_error(exc)) from exc
    raise ValueError(f"属性表编码无法识别（已尝试 {'、'.join(_ENCODINGS)}）：{last_error}")


def _detect_epsg(frame: Any, role: str) -> int:
    """确定源数据坐标系；.prj 无法识别 EPSG 时按 CGCS2000 3 度带 40 带兜底。"""
    crs = frame.crs
    if crs is None:
        raise ValueError(f"{layer_label(role)} 缺少坐标系，不能猜测位置。")
    epsg = crs.to_epsg()
    if epsg:
        return int(epsg)
    name = str(getattr(crs, "name", "") or "")
    upper = name.upper()
    if "CGCS2000" in upper and "ZONE_40" in upper:
        return DEFAULT_SOURCE_EPSG
    raise ValueError(
        f"{layer_label(role)} 的坐标系无法识别（{name or '未知'}），"
        f"请在 .prj 中写明 CGCS2000 3 度带 40 带。"
    )


def _normalize_frame(frame: Any, role: str) -> tuple[Any, list[str]]:
    """列名统一大写、补齐可选字段、清理无效几何，返回 (表, 警告)。"""
    import shapely

    warnings: list[str] = []
    geom_name = frame.geometry.name
    frame = frame.rename(columns={c: str(c).upper() for c in frame.columns if c != geom_name})

    spec = LAYERS[role]
    required = list(spec.get("fields", ()))
    missing = [f for f in required if f not in frame.columns]
    if missing:
        raise ValueError(
            f"{layer_label(role)} 缺少必需字段：{'、'.join(missing)}"
            f"（必需：{'、'.join(required)}）。请确认上传的是带属性表的 Shapefile 或 GeoJSON。"
        )

    optional_fields = list(
        HSX_OPTIONAL_FIELDS if role == "hsx" else QSX_OPTIONAL_FIELDS if role == "qsx" else ()
    )
    empty = [f for f in optional_fields if f not in frame.columns]
    for field_name in empty:
        frame[field_name] = ""
    if empty:
        warnings.append(f"{layer_label(role)} 缺少可选字段 {'、'.join(empty)}，已置空。")

    # 几何：丢掉空几何、强制二维；无效几何交给库内 ST_MakeValid 修复。
    frame = frame[~frame.geometry.is_empty & frame.geometry.notna()].copy()
    frame.geometry = shapely.force_2d(frame.geometry.values)
    invalid = int((~frame.geometry.is_valid).sum())
    if invalid:
        warnings.append(f"{layer_label(role)} 有 {invalid} 个无效几何，入库前统一修复。")

    if role in ("hsx", "qsx"):
        blank = frame["XZQDM"].isna() | (frame["XZQDM"].astype(str).str.strip() == "")
        if int(blank.sum()):
            raise ValueError(
                f"{layer_label(role)} 有 {int(blank.sum())} 条记录缺少 XZQDM（县级行政区代码），"
                "入库表要求该字段非空。"
            )
    return frame, warnings


def check_text_widths(frame: Any, fields: Iterable[str]) -> list[str]:
    """检查待入库文本字段是否超出目标表列宽，超出即报错（不做静默截断）。"""
    problems: list[str] = []
    for field_name in fields:
        limit = TEXT_WIDTHS.get(field_name)
        if not limit or field_name not in frame.columns:
            continue
        lengths = frame[field_name].dropna().astype(str).str.len()
        longest = int(lengths.max()) if len(lengths) else 0
        if longest > limit:
            problems.append(f"{field_name} 最长 {longest} 字符，超过表列宽 {limit}")
    return problems


def read_layer(paths: Sequence[Path], role: str) -> LayerData:
    """读取一个输入图层并标准化（同步函数，调用方放线程池执行）。

    ZIP 会先解压到临时目录再读：既能让 GDAL 补写 .shx，也避免在只读虚拟路径上失败。
    """
    _configure_gdal()
    with tempfile.TemporaryDirectory(prefix="geoagent_layer_") as workspace:
        source, shown, source_warnings = _geo_source(paths, Path(workspace), role)
        is_shp = source.lower().endswith(".shp")
        frame, encoding = _read_with_encoding_fallback(source, is_shp)
        if frame is None or len(frame) == 0:
            raise ValueError(f"{layer_label(role)} 没有要素。")
        epsg = _detect_epsg(frame, role)
        frame, warnings = _normalize_frame(frame, role)
    if frame.empty:
        raise ValueError(f"{layer_label(role)} 没有有效几何要素。")
    info = LayerInfo(
        role=role,
        label=layer_label(role),
        rows=int(len(frame)),
        source=shown,
        encoding=encoding,
        epsg=int(epsg),
        fields=[str(c) for c in frame.columns if c != frame.geometry.name],
        warnings=source_warnings + warnings,
    )
    return LayerData(role=role, frame=frame, info=info)


def _clean_text(value: Any) -> Optional[str]:
    """把属性值转成文本；空值统一为 NULL，避免把 NaN 写进 varchar。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:  # pragma: no cover - 非标量值不参与文本转换
        pass
    text = str(value).strip()
    return text or None


def frame_records(frame: Any, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """把 GeoDataFrame 转成 COPY 记录：属性列 + WKB（二进制）。"""
    import shapely

    geometry = shapely.force_2d(frame.geometry.values)
    wkb = shapely.to_wkb(geometry, hex=False)
    values = {name: [_clean_text(v) for v in frame[name].tolist()] for name in columns}
    records: list[tuple[Any, ...]] = []
    for index in range(len(frame)):
        records.append(tuple(values[name][index] for name in columns) + (wkb[index],))
    return records