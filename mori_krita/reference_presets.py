"""Reference purposes shared by the native picker and generation instructions."""

CANVAS_SOURCE = "canvas"
REFERENCE_PRESETS = (
    ("style", "风格参考", "只参考画风、笔触与表现方式，不照搬主体或构图。",
     "Use only its visual style, brushwork and rendering language; do not copy its subject or composition."),
    ("identity", "主体一致性", "保持人物五官、服装或产品的外形特征。",
     "Preserve the depicted character or product identity, facial features, clothing and recognizable shape."),
    ("material", "材质参考", "参考表面纹理、质感与反射特征。",
     "Use only its surface material, texture, finish and reflectance; keep the main drawing's layout."),
    ("palette", "配色参考", "参考整体配色与颜色关系。",
     "Use only its color palette and color relationships; do not copy its objects or layout."),
    ("lighting", "光影参考", "参考光线方向、明暗与氛围。",
     "Use only its light direction, contrast, shadows and atmosphere."),
    ("composition", "构图参考", "参考空间与镜头关系；优先于默认画布构图。",
     "Use its spatial arrangement and camera framing as the composition guide, while preserving the main subject."),
)
PRESET_BY_ID = {item[0]: item for item in REFERENCE_PRESETS}
