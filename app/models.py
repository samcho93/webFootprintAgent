"""부품 사양(PartSpec) 데이터 모델.

데이터시트 추출 → 생성 → 검토 → 검증 파이프라인 전체에서 공유하는 단일 진실 원천(single source of truth).
모든 치수 단위는 mm.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ElectricalType = Literal[
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter", "no_connect",
]
PinSide = Literal["left", "right", "top", "bottom", "auto"]

PackageFamily = Literal[
    "gullwing_dual",    # SOIC, SOP, SSOP, TSSOP, MSOP, SOT-23-5/6/8 처럼 양쪽 대칭 걸윙
    "gullwing_quad",    # QFP, LQFP, TQFP
    "nolead_dual",      # DFN, SON, WSON
    "nolead_quad",      # QFN, VQFN, WQFN
    "chip_2terminal",   # 0402/0603 칩 R/C/L, SOD-123, SMA/SMB, 칩 LED
    "dip",              # DIP/PDIP 스루홀
    "pin_header",       # 핀 헤더 / 단순 격자 THT 커넥터
    "bga",              # BGA / WLCSP
    "explicit",         # 불규칙 패키지: 패드 좌표를 직접 지정 (SOT-23-3, SOT-223, TO-252, 커넥터 등)
]


class Pin(BaseModel):
    number: str = Field(description="핀 번호 (풋프린트 패드 번호와 일치해야 함). 예: '1', 'A3', 'EP'")
    name: str = Field(description="데이터시트 핀 이름. 반전 신호는 ~{RESET} 형식")
    electrical_type: ElectricalType
    side: PinSide = Field(default="auto", description="심볼 상의 배치 방향")
    group: str = Field(default="", description="기능 그룹 (예: 'Power', 'GPIO', 'SPI')")
    description: str = Field(default="")


class ExplicitPad(BaseModel):
    number: str = Field(description="패드 번호. 기구 고정용 패드는 빈 문자열")
    x: float = Field(description="패키지 중심 기준 X (mm, 오른쪽 +)")
    y: float = Field(description="패키지 중심 기준 Y (mm, 아래쪽 +, KiCad 좌표계)")
    width: float
    height: float
    shape: Literal["rect", "roundrect", "circle", "oval"] = "roundrect"
    pad_type: Literal["smd", "thru_hole", "np_thru_hole"] = "smd"
    drill: Optional[float] = Field(default=None, description="THT 드릴 지름 (mm)")
    rotation: float = 0.0


class ExposedPad(BaseModel):
    width: float = Field(description="X 방향 크기 (mm)")
    length: float = Field(description="Y 방향 크기 (mm)")
    number: str = Field(default="", description="EP 패드 번호. 비면 pin_count+1")


class PackageSpec(BaseModel):
    name: str = Field(description="패키지 이름 예: 'SOIC-8', 'QFN-24 4x4'")
    family: PackageFamily
    pin_count: int = Field(description="EP 제외 신호 단자 수")
    pitch: Optional[float] = None
    body_length: Optional[float] = Field(default=None, description="D: 핀 배열 방향 몸체 길이 (Y)")
    body_width: Optional[float] = Field(default=None, description="E1: 몸체 폭 (X)")
    lead_span_min: Optional[float] = Field(default=None, description="E: 리드 끝-끝 전체 폭 최소 (걸윙/QFP)")
    lead_span_max: Optional[float] = None
    lead_length_min: Optional[float] = Field(default=None, description="L: 랜딩 리드(단자) 길이 최소")
    lead_length_max: Optional[float] = None
    lead_width_min: Optional[float] = Field(default=None, description="b: 리드 폭 최소")
    lead_width_max: Optional[float] = None
    height_max: Optional[float] = Field(default=None, description="A: 최대 높이")
    pins_x: Optional[int] = Field(default=None, description="쿼드 패키지에서 위/아래 변 하나당 핀 수")
    pins_y: Optional[int] = Field(default=None, description="쿼드 패키지에서 좌/우 변 하나당 핀 수")
    row_spacing: Optional[float] = Field(default=None, description="DIP/헤더 열 간격 (중심-중심)")
    rows: Optional[int] = Field(default=None, description="BGA/헤더 행 수")
    cols: Optional[int] = Field(default=None, description="BGA/헤더 열 수")
    ball_diameter: Optional[float] = None
    missing_balls: list[str] = Field(default_factory=list, description="BGA 비어있는 볼 위치 (예: 'B2')")
    lead_diameter: Optional[float] = Field(default=None, description="THT 리드 지름/대각선 최대")
    exposed_pad: Optional[ExposedPad] = None
    explicit_pads: list[ExplicitPad] = Field(default_factory=list)
    land_pattern_source: Literal["datasheet_recommended", "ipc7351_calculated"] = "ipc7351_calculated"
    notes: str = ""


class PartSpec(BaseModel):
    part_number: str
    manufacturer: str = ""
    description: str = ""
    datasheet_url: str = ""
    reference_prefix: str = Field(default="U", description="U, R, C, D, Q, J, ...")
    keywords: str = ""
    pins: list[Pin]
    package: PackageSpec
    evidence: list[str] = Field(default_factory=list, description="근거 페이지/표 메모 (예: 'p.3 Table 4-1 Pin Functions')")
    extraction_notes: str = ""


# ---------- 검토(Review) 결과 ----------
class ReviewIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    area: Literal["pins", "package", "land_pattern", "symbol", "metadata"]
    description: str
    evidence: str = ""


class ReviewResult(BaseModel):
    verdict: Literal["pass", "fail"]
    summary: str
    issues: list[ReviewIssue]
    corrected_spec: Optional[PartSpec] = Field(
        default=None, description="error 가 있을 때만: 수정된 전체 사양. 없으면 null"
    )


# ---------- 데이터시트 검색 결과 ----------
class DatasheetCandidate(BaseModel):
    url: str
    title: str = ""
    source: str = Field(default="", description="manufacturer / distributor / other")


class DatasheetSearchResult(BaseModel):
    part_number: str
    manufacturer: str
    candidates: list[DatasheetCandidate]
    notes: str = ""
