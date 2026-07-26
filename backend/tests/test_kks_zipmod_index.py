from __future__ import annotations

import importlib.util
from pathlib import Path


def _indexer():
    path = Path(__file__).parents[1] / "tools" / "build_kks_zipmod_index.py"
    spec = importlib.util.spec_from_file_location("test_kks_zipmod_index_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_character_maker_color_mask_rows_are_indexed() -> None:
    indexer = _indexer()
    text = "\n".join(
        [
            "409",
            "0",
            "Assets/Illusion/assetbundle/chara/list/characustom/00/mt_eye_gradation_00.bytes",
            "ID,Kind,Possess,Name,ColorMaskAB,ColorMaskTex,ThumbAB,ThumbTex",
            "1883,0,1,Type D,chara/_kero/CMS_NT_00.unity3d,cw_t_hitomigrad_00d,chara/_kero/CMS_NT_00.unity3d,thumb_hitomigrad_00d",
        ]
    )

    rows = indexer._list_rows(text, "abdata/list/characustom/NT_CMS_00_mt_eye_gradation_00.csv")

    assert rows == [
        (
            409,
            {
                "ID": "1883",
                "Kind": "0",
                "Name": "Type D",
                "ColorMaskAB": "chara/_kero/CMS_NT_00.unity3d",
                "ColorMaskTex": "cw_t_hitomigrad_00d",
            },
        )
    ]
