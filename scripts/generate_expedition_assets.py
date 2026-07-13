from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "assets" / "expedition" / "heroes"
HEROES = [
    ("jinxiao", "烬绡", "#ff7b67", "#ffd166", "ember"), ("chengyin", "澄音", "#72d6cc", "#d7fff5", "halo"),
    ("yaye", "鸦夜", "#a78bfa", "#242144", "feather"), ("leishan", "垒山", "#d39b68", "#35404c", "shield"),
    ("xianlv", "弦律", "#63c5da", "#214b67", "strings"), ("gulu", "菌王咕噜", "#9cd26b", "#314e3c", "spore"),
    ("shuangli", "霜璃", "#9bdcff", "#3a5f9d", "ice"), ("tuantuan", "团团兽", "#ffb9d1", "#714b67", "ribbon"),
    ("zhenchui", "震槌", "#f4b860", "#54402c", "hammer"), ("yaoge", "曜歌", "#ffe88a", "#745ea8", "halo"),
    ("xueji", "血棘", "#f25f74", "#5e2333", "thorn"), ("mochai", "墨裁", "#718096", "#202b42", "ink"),
    ("huidun", "辉盾", "#e9f0ff", "#5475a7", "guard"), ("shashi", "砂时", "#e2b876", "#66523b", "hourglass"),
    ("jinglan", "镜澜", "#6de3e3", "#244f6b", "mirror"), ("lan_ying", "兰荧", "#b2f06d", "#32634e", "leaf"),
    ("jiqiao7", "机巧七号", "#a8d8ff", "#334a62", "drone"), ("leiyuan", "雷丸", "#ffe66d", "#3159a5", "bolt"),
    ("zhiyuan", "纸鸢", "#ff9fba", "#674667", "kite"), ("xuanjia", "玄甲", "#a5b4c9", "#253140", "armor"),
    ("lingxi", "灵汐", "#74f2c3", "#285d62", "tide"), ("heluo", "赫罗", "#ff6b6b", "#4f2739", "dice"),
    ("mianxing", "眠星", "#b9a8ff", "#34315b", "star"), ("naipaolong", "奶泡龙", "#f4d7b5", "#8f6280", "bubble"),
]


def svg(hero_id: str, name: str, primary: str, secondary: str, motif: str, kind: str) -> str:
    digest = hashlib.sha256(hero_id.encode()).hexdigest(); a = int(digest[:2], 16) % 26 - 13; b = int(digest[2:4], 16) % 22 - 11
    common = f'''<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#0d2425"/><stop offset="1" stop-color="{secondary}" stop-opacity=".92"/></linearGradient><linearGradient id="coat" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{primary}"/><stop offset="1" stop-color="{secondary}"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'''
    motif_shapes = {
        "ember": f'<path d="M38 132 Q145 58 252 132" stroke="{primary}" stroke-width="8" fill="none" opacity=".8"/><path d="M66 135 L98 78 116 134 M184 134 L214 72 236 134" stroke="{secondary}" stroke-width="5"/>',
        "halo": f'<circle cx="145" cy="77" r="42" fill="none" stroke="{primary}" stroke-width="7" opacity=".8"/><circle cx="145" cy="77" r="56" fill="none" stroke="{secondary}" opacity=".38"/>',
        "feather": f'<path d="M44 137 Q78 36 145 112 Q213 36 246 137" fill="none" stroke="{primary}" stroke-width="9"/><path d="M145 38v108" stroke="{secondary}" stroke-width="4"/>',
        "shield": f'<path d="M145 28 230 62v65q-24 42-85 59-61-17-85-59V62z" fill="none" stroke="{primary}" stroke-width="8"/><path d="M145 45v130" stroke="{secondary}" opacity=".7" stroke-width="5"/>',
        "strings": f'<path d="M42 66 Q145 180 248 66 M42 101 Q145 215 248 101" fill="none" stroke="{primary}" stroke-width="5"/><circle cx="145" cy="92" r="16" fill="{secondary}"/>',
        "spore": f'<circle cx="67" cy="71" r="18" fill="{primary}"/><circle cx="224" cy="64" r="26" fill="{primary}"/><circle cx="60" cy="132" r="9" fill="{secondary}"/><circle cx="235" cy="133" r="11" fill="{secondary}"/>',
        "ice": f'<path d="M145 20v150M60 70l170 100M230 70 60 170" stroke="{primary}" stroke-width="6"/><path d="M145 38 128 62h34zM145 158l-17-24h34z" fill="{secondary}"/>',
        "ribbon": f'<path d="M46 80 Q145 20 244 80 Q145 142 46 80z" fill="none" stroke="{primary}" stroke-width="8"/><circle cx="145" cy="80" r="19" fill="{secondary}"/>',
        "hammer": f'<path d="M65 40 224 158M72 35l52-19 24 25-54 19z" stroke="{primary}" stroke-width="10"/><circle cx="215" cy="157" r="18" fill="{secondary}"/>',
        "thorn": f'<path d="M42 155 Q105 20 145 155 Q186 20 248 155" fill="none" stroke="{primary}" stroke-width="9"/><path d="M145 45v110" stroke="{secondary}" stroke-width="4"/>',
        "ink": f'<path d="M44 126 Q91 25 145 98 Q199 25 246 126" fill="none" stroke="{primary}" stroke-width="12"/><path d="M76 152h138" stroke="{secondary}" stroke-width="8"/>',
        "guard": f'<rect x="74" y="45" width="142" height="104" rx="22" fill="none" stroke="{primary}" stroke-width="8"/><path d="M145 45v104" stroke="{secondary}" stroke-width="6"/>',
        "hourglass": f'<path d="M83 35h124M83 165h124M92 39q0 42 53 66-53 24-53 56M198 39q0 42-53 66 53 24 53 56" fill="none" stroke="{primary}" stroke-width="7"/>',
        "mirror": f'<path d="M145 25 232 112l-87 63-87-63z" fill="none" stroke="{primary}" stroke-width="8"/><path d="M145 25v150" stroke="{secondary}" stroke-width="5"/>',
        "leaf": f'<path d="M45 142Q145 18 245 142Q145 122 45 142z" fill="none" stroke="{primary}" stroke-width="8"/><path d="M145 40v112" stroke="{secondary}" stroke-width="5"/>',
        "drone": f'<rect x="81" y="54" width="128" height="94" rx="16" fill="none" stroke="{primary}" stroke-width="8"/><circle cx="113" cy="101" r="10" fill="{secondary}"/><circle cx="177" cy="101" r="10" fill="{secondary}"/>',
        "bolt": f'<path d="M163 24 77 113h57l-18 63 86-94h-57z" fill="{primary}" opacity=".8"/><circle cx="145" cy="98" r="46" fill="none" stroke="{secondary}" stroke-width="5"/>',
        "kite": f'<path d="M145 24 228 105 145 171 62 105z" fill="none" stroke="{primary}" stroke-width="8"/><path d="M145 171q30 20 7 38" fill="none" stroke="{secondary}" stroke-width="5"/>',
        "armor": f'<path d="M75 41h140v100l-70 36-70-36z" fill="none" stroke="{primary}" stroke-width="9"/><path d="M102 61v70m86-70v70" stroke="{secondary}" stroke-width="5"/>',
        "tide": f'<path d="M35 110q40-50 80 0t80 0 80 0M35 145q40-50 80 0t80 0 80 0" fill="none" stroke="{primary}" stroke-width="8"/>',
        "dice": f'<rect x="82" y="45" width="126" height="126" rx="20" fill="none" stroke="{primary}" stroke-width="9"/><circle cx="116" cy="79" r="9" fill="{secondary}"/><circle cx="174" cy="137" r="9" fill="{secondary}"/><circle cx="145" cy="108" r="9" fill="{secondary}"/>',
        "star": f'<path d="m145 25 24 51 56 7-41 38 12 56-51-27-51 27 12-56-41-38 56-7z" fill="none" stroke="{primary}" stroke-width="7"/>',
        "bubble": f'<circle cx="105" cy="91" r="42" fill="none" stroke="{primary}" stroke-width="8"/><circle cx="185" cy="130" r="24" fill="none" stroke="{secondary}" stroke-width="6"/><circle cx="208" cy="66" r="11" fill="{primary}"/>',
    }[motif]
    if kind == "portrait":
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 290 360" role="img" aria-label="{name} portrait">{common}<rect width="290" height="360" rx="28" fill="url(#bg)"/><g opacity=".72" filter="url(#glow)">{motif_shapes}</g><path d="M66 360q9-100 79-116 70 16 79 116" fill="url(#coat)"/><path d="M92 188q10-62 53-66 43 4 53 66v52q-23 35-53 35t-53-35z" fill="#f2c9ac"/><path d="M88 187q7-91 57-100 56 19 58 100l-28-32-28 25-33-25z" fill="{secondary}"/><circle cx="125" cy="193" r="5" fill="#152328"/><circle cx="165" cy="193" r="5" fill="#152328"/><path d="M128 222q17 11 34 0" stroke="#7d4052" stroke-width="4" fill="none"/><path d="M95 279q50 29 100 0" stroke="{primary}" stroke-width="7" fill="none"/><text x="24" y="332" fill="#effff9" font-family="sans-serif" font-size="17" font-weight="700">{name}</text><text x="24" y="352" fill="{primary}" font-family="monospace" font-size="9">MENTORFLOW / {hero_id.upper()}</text></svg>'''
    if kind == "avatar":
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 160" role="img" aria-label="{name} avatar"><defs><clipPath id="c"><circle cx="80" cy="80" r="75"/></clipPath></defs><circle cx="80" cy="80" r="76" fill="{secondary}" stroke="{primary}" stroke-width="5"/><g clip-path="url(#c)">{motif_shapes}<circle cx="80" cy="84" r="38" fill="#f2c9ac"/><path d="M34 86Q42 25 80 30q46 2 48 56l-20-17-28 19-26-20z" fill="{secondary}"/><path d="M30 160q8-54 50-60 42 6 50 60" fill="url(#coat)"/></g></svg>'''
    if kind == "skill_icon":
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img" aria-label="{name} skill"><rect x="5" y="5" width="86" height="86" rx="22" fill="url(#bg)" stroke="{primary}" stroke-width="4"/>{motif_shapes.replace('145', '48').replace('230', '78').replace('60', '18').replace('248', '78').replace('42', '18').replace('245', '78').replace('35', '18').replace('240', '78').replace('44', '18').replace('246', '78').replace('45', '18').replace('214', '72').replace('82', '25').replace('208', '70').replace('81', '25').replace('209', '71').replace('215', '72').replace('207', '71').replace('224', '75').replace('236', '78').replace('252', '80').replace('250', '80').replace('228', '76').replace('232', '77').replace('215', '72').replace('216', '72').replace('207', '70').replace('208', '70').replace('248', '78').replace('246', '78').replace('244', '78').replace('242', '78').replace('230', '76').replace('228', '76').replace('224', '74').replace('220', '73').replace('213', '71').replace('210', '70').replace('208', '69').replace('207', '69').replace('198', '66').replace('200', '67').replace('186', '62').replace('184', '61').replace('170', '57').replace('165', '55').replace('160', '53').replace('154', '51').replace('150', '50').replace('140', '47').replace('130', '43').replace('120', '40').replace('110', '37').replace('100', '33').replace('90', '30').replace('80', '27').replace('70', '23').replace('66', '22').replace('65', '21').replace('64', '21').replace('62', '20').replace('61', '20').replace('58', '19').replace('57', '19').replace('56', '18').replace('55', '18').replace('54', '18').replace('53', '18').replace('52', '17').replace('51', '17').replace('50', '17').replace('49', '16').replace('48', '16').replace('47', '16').replace('46', '15').replace('40', '13').replace('38', '13').replace('36', '12').replace('34', '11').replace('32', '11').replace('30', '10').replace('28', '9').replace('26', '9').replace('24', '8').replace('22', '7').replace('20', '7').replace('18', '6').replace('16', '5').replace('14', '5').replace('12', '4').replace('10', '3').replace('9', '3').replace('8', '3').replace('7', '2').replace('6', '2').replace('5', '2').replace('4', '1').replace('3', '1').replace('2', '1').replace('1', '1')}<circle cx="48" cy="48" r="10" fill="{primary}" opacity=".7"/></svg>'''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 240" role="img" aria-label="{name} skill cut in"><rect width="640" height="240" fill="url(#bg)"/><path d="M0 205Q180 70 360 205T720 160" fill="none" stroke="{primary}" stroke-width="12" opacity=".45"/><g transform="translate(280 35) scale(.8)">{motif_shapes}</g><text x="30" y="195" fill="#effff9" font-family="sans-serif" font-size="30" font-weight="700">{name}</text><text x="32" y="222" fill="{primary}" font-family="monospace" font-size="12">SKILL CUT-IN · {hero_id.upper()}</text></svg>'''


def main() -> None:
    for hero_id, name, primary, secondary, motif in HEROES:
        folder = OUT / hero_id; folder.mkdir(parents=True, exist_ok=True)
        for kind in ("portrait", "avatar", "skill_icon", "skill_cut_in"):
            (folder / f"{kind}.svg").write_text(svg(hero_id, name, primary, secondary, motif, kind), encoding="utf-8")
    print(f"generated {len(HEROES) * 4} expedition SVG assets in {OUT}")


if __name__ == "__main__": main()
