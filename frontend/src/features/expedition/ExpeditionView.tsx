import { useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowRight, BookOpen, Box, CheckCircle2, Coins, Crosshair, Crown, Gem, Palette, RotateCcw, Save, Shield, Sparkles, Swords, Upload, Users, WandSparkles } from "lucide-react"
import { BattleReplay } from "./BattleReplay"
import { expeditionApi, type ArtPackVersion, type AssetKind, type BattleEvent, type BattleUnit, type ExpeditionProfile, type GachaBanner, type Hero, type Lineup, type Stage, unwrapProfile } from "./api"
import { fallbackAssetUrl, resolveExpeditionAsset } from "./assetResolver"
import "./expedition.css"
import "./expedition-art.css"
import "./expedition-art-warm.css"

type View = "home" | "heroes" | "formation" | "gacha" | "stages" | "inventory" | "art-review"
// The player faces right: row 1 / column 1 is the right-most front slot.
// Keep the persisted cell ids stable, but render each row from the player's view.
const formationCells = Array.from({ length: 3 }, (_, row) => [0, 1, 2].map(column => `cell_${row * 3 + (2 - column)}`)).flat()
const cellLabel = (cell: string) => {
  const index = Number(cell.replace("cell_", ""))
  const row = Math.floor(index / 3) + 1
  const column = (index % 3) + 1
  return `第 ${row} 行 · 第 ${column} 列${index === 0 ? " · 前排右侧" : ""}`
}

function errorText(error: unknown) { return error instanceof Error ? error.message : "操作失败，请稍后再试" }
function heroPortrait(hero?: Hero, size = "medium", kind: AssetKind = "portrait") {
  // Small combat/list tokens should use the square avatar crop; full portraits
  // remain reserved for the codex and expedition hero banner.
  const assetKind = size === "small" && kind === "portrait" ? "avatar" : kind
  const asset = resolveExpeditionAsset(hero, assetKind)
  const looksLikePlaceholder = !asset.fallback_used && asset.width > 0 && asset.width <= 256
  return <div className={`expedition-hero-portrait ${size} ${asset.fallback_used ? "fallback-asset" : "official-asset"} ${looksLikePlaceholder ? "placeholder-asset" : "image-asset"}`} data-asset-version={asset.version} data-asset-kind={assetKind}>
    <img src={asset.url} alt={`${hero?.name || "角色"} ${assetKind}`} loading="lazy" decoding="async" onError={event => { const target = event.currentTarget; target.style.display = "none"; target.parentElement?.classList.add("image-load-failed") }} />
    <span>{hero?.name?.slice(0, 1) || "?"}</span>
  </div>
}

export function ExpeditionView() {
  const [view, setView] = useState<View>("home")
  const [profile, setProfile] = useState<ExpeditionProfile | null>(null)
  const [heroes, setHeroes] = useState<Hero[]>([])
  const [lineups, setLineups] = useState<Lineup[]>([])
  const [banners, setBanners] = useState<GachaBanner[]>([])
  const [stage, setStage] = useState<Stage | null>(null)
  const [stageNumber, setStageNumber] = useState(1)
  const [ledger, setLedger] = useState<Array<Record<string, unknown>>>([])
  const [milestones, setMilestones] = useState<Array<{ milestone_id: string; label: string; required_minutes: number; claimed: boolean; ticket_type?: string; claimable_count?: number }>>([])
  const [equipment, setEquipment] = useState<{ definitions: Array<Record<string, unknown>>; instances: Array<Record<string, unknown>> }>({ definitions: [], instances: [] })
  const [pets, setPets] = useState<{ definitions: Array<Record<string, unknown>>; owned: Array<Record<string, unknown>> }>({ definitions: [], owned: [] })
  const [artPacks, setArtPacks] = useState<ArtPackVersion[]>([])
  const [activeArtPack, setActiveArtPack] = useState<ArtPackVersion | null>(null)
  const [selectedHeroId, setSelectedHeroId] = useState("jinxiao")
  const [draftLineup, setDraftLineup] = useState<Lineup | null>(null)
  const [pullResult, setPullResult] = useState<Array<Record<string, unknown>>>([])
  const [events, setEvents] = useState<BattleEvent[]>([])
  const [battle, setBattle] = useState<{ battle_id: string; winner: string; stage_number: number; units: BattleUnit[] } | null>(null)
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setBusy(true)
    try {
      const [profilePayload, heroPayload, lineupPayload, bannerPayload, stagePayload, ledgerPayload, milestonePayload, equipmentPayload, petPayload, artPackPayload, activePackPayload] = await Promise.all([
        expeditionApi.profile(), expeditionApi.heroes(), expeditionApi.lineups(), expeditionApi.banners(), expeditionApi.stages(), expeditionApi.ledger(), expeditionApi.milestones(), expeditionApi.equipment(), expeditionApi.pets(), expeditionApi.artPacks(), expeditionApi.activeArtPack(),
      ])
      const nextProfile = unwrapProfile(profilePayload)
      setProfile(nextProfile); setHeroes(heroPayload.items); setLineups(lineupPayload.items); setBanners(bannerPayload.items); setStage(stagePayload.current); setStageNumber(stagePayload.current_stage); setLedger(ledgerPayload.items); setMilestones(milestonePayload.items); setEquipment(equipmentPayload); setPets(petPayload); setArtPacks(artPackPayload.items); setActiveArtPack(activePackPayload.active)
      const active = lineupPayload.items.find(item => item.active) || lineupPayload.items[0]
      if (active) setDraftLineup(active)
    } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) }
  }
  useEffect(() => { void load() }, [])

  const ownedHeroes = useMemo(() => heroes.filter(hero => hero.owned), [heroes])
  const selectedHero = heroes.find(hero => hero.hero_id === selectedHeroId) || ownedHeroes[0]
  const selectView = (next: View) => { setNotice(""); setView(next) }
  const refreshProfile = async () => { const result = await expeditionApi.profile(); setProfile(unwrapProfile(result)) }

  async function upgradeSlot() { try { const result = await expeditionApi.upgradeSlot(); setProfile(unwrapProfile(result)); setNotice("全局训练等级已升级，所有角色同步生效") } catch (error) { setNotice(errorText(error)) } }
  async function saveFormation() {
    if (!draftLineup) return
    try { setBusy(true); const result = await expeditionApi.saveLineup({ lineup_id: draftLineup.lineup_id, name: draftLineup.name, stance: draftLineup.stance, members: draftLineup.members, expected_revision: draftLineup.revision }); setLineups(current => current.map(item => item.lineup_id === result.item.lineup_id ? result.item : item)); setDraftLineup(result.item); setNotice("阵容已保存；战斗开始后会锁定阵容") } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) }
  }
  async function pull(banner: GachaBanner, count: 1 | 10, currency: "crystals" | "ticket" = "crystals") { try { setBusy(true); const result = await expeditionApi.pull(banner.banner_id, count, currency); setPullResult(result.results); await refreshProfile(); await load(); setView("gacha") } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) } }
  async function challenge() { try { setBusy(true); const result = await expeditionApi.challenge(stageNumber); const replay = await expeditionApi.battleEvents(result.battle_id); setEvents(replay.events); setBattle({ battle_id: result.battle_id, winner: result.result?.winner || "", stage_number: stageNumber, units: result.result?.units || [] }); await load(); setNotice(result.result?.winner === "ally" ? "关卡已通关，战斗事件已落盘" : "本次挑战失败，可以调整阵容后重试"); } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) } }
  async function claimMilestone(id: string) { try { await expeditionApi.claimMilestone(id); setNotice("里程碑奖励已领取"); await load() } catch (error) { setNotice(errorText(error)) } }

  return <div className="expedition-page" data-testid="expedition-page">
    <div className="expedition-page-head"><div><span className="expedition-kicker">LEARNING EXPEDITION · V1</span><h1>把资料变成一场可游玩的远征</h1><p>学习 Run 验证的每一分钟，都会进入训练槽与角色养成；战斗结果由后端事件流决定。</p></div><button className="expedition-icon-button" onClick={() => void load()} disabled={busy} title="刷新远征数据"><RotateCcw size={17} /></button></div>
    <ArtStatus active={activeArtPack} />
    <div className="expedition-resource-bar"><div><Gem size={16} /><span>学习水晶</span><b>{profile?.crystals ?? 0}</b></div><div><Sparkles size={16} /><span>训练经验</span><b>{profile?.training_exp ?? 0}</b></div><div><BookOpen size={16} /><span>已验证学习</span><b>{profile?.total_verified_minutes ?? 0} 分钟</b></div><div><Crosshair size={16} /><span>无限关卡</span><b>第 {profile?.current_stage ?? stageNumber} 关</b></div></div>
    <nav className="expedition-tabs" aria-label="远征模块"><Tab active={view === "home"} icon={<Sparkles size={15} />} label="远征大厅" onClick={() => selectView("home")} /><Tab active={view === "heroes"} icon={<Users size={15} />} label="角色与训练" onClick={() => selectView("heroes")} /><Tab active={view === "formation"} icon={<Shield size={15} />} label="九宫格阵容" onClick={() => selectView("formation")} /><Tab active={view === "gacha"} icon={<Gem size={15} />} label="招募卡池" onClick={() => selectView("gacha")} /><Tab active={view === "stages"} icon={<Swords size={15} />} label="无限闯关" onClick={() => selectView("stages")} /><Tab active={view === "inventory"} icon={<Box size={15} />} label="装备与宠物" onClick={() => selectView("inventory")} /><Tab active={view === "art-review"} icon={<Palette size={15} />} label="美术审核" onClick={() => selectView("art-review")} /></nav>
    {notice && <div className="expedition-notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}
    {view === "home" && <Home profile={profile} heroes={ownedHeroes} milestones={milestones} ledger={ledger} onView={selectView} onClaim={claimMilestone} onUpgrade={upgradeSlot} />}
    {view === "heroes" && <Heroes heroes={heroes} selected={selectedHero} onSelect={hero => { setSelectedHeroId(hero.hero_id); void expeditionApi.hero(hero.hero_id).then(detail => setHeroes(current => current.map(item => item.hero_id === detail.hero_id ? { ...item, ...detail } : item))).catch(() => {}) }} />}
    {view === "formation" && <Formation heroes={ownedHeroes} lineups={lineups} draft={draftLineup} setDraft={setDraftLineup} onSave={saveFormation} onActivate={async id => { try { await expeditionApi.activateLineup(id); setNotice("已切换为主动阵容"); await load() } catch (error) { setNotice(errorText(error)) } }} />}
    {view === "gacha" && <Gacha banners={banners} profile={profile} result={pullResult} onPull={pull} heroes={heroes} />}
    {view === "stages" && <Stages stage={stage} stageNumber={stageNumber} setStageNumber={setStageNumber} onChallenge={challenge} busy={busy} battle={battle} events={events} heroes={heroes} />}
    {view === "inventory" && <Inventory equipment={equipment} pets={pets} />}
    {view === "art-review" && <><ArtReview heroes={heroes} packs={artPacks} active={activeArtPack} onRefresh={load} /><ArtReviewDiagnostics heroes={heroes} packs={artPacks} active={activeArtPack} /></>}
  </div>
}

function ArtStatus({ active }: { active: ArtPackVersion | null }) {
  // `dev_art_fixture` is the local registry source for the reviewed real art pack.
  // It must not be presented as a placeholder when an ACTIVE pack is installed.
  const isDevFixture = false
  return <div className={`expedition-art-status ${isDevFixture ? "preview" : "ready"}`} role="status">
    <span className="expedition-art-status-dot" />
    <div><b>{active ? `当前美术包 · ${active.version}` : "当前使用基础占位美术"}</b><small>{isDevFixture ? "当前激活的是测试占位包，所以会看到纯色头像；1.1.0 高清资源仍需完成审核后激活。" : active ? "角色图片已接入正式资源管线。" : "安装并激活美术包后，这里会显示角色立绘和技能图标。"}</small></div>
    {isDevFixture && <span className="expedition-art-status-tag">DEV FIXTURE</span>}
  </div>
}


function Home({ profile, heroes, milestones, ledger, onView, onClaim, onUpgrade }: { profile: ExpeditionProfile | null; heroes: Hero[]; milestones: Array<{ milestone_id: string; label: string; required_minutes: number; claimed: boolean; claimable_count?: number }>; ledger: Array<Record<string, unknown>>; onView: (view: View) => void; onClaim: (id: string) => void; onUpgrade: () => void }) {
  const dailyVerified = profile?.daily_verified_minutes || 0
  const training = profile?.training
  const equipmentTickets = profile?.tickets?.equipment || 0
  const characterTickets = profile?.tickets?.character || 0
  return <div className="expedition-home-grid"><section className="expedition-hero-banner"><div className="expedition-banner-copy"><span className="expedition-kicker">THE ARCHIVE BECOMES A WORLD</span><h2>今天的学习，不是清单，是一段会留下战果的旅程。</h2><p>完成资料驱动的 Learning Run 后，系统自动把验证通过的学习分钟写入全局训练等级和每日十连券进度。</p><div className="expedition-quick-actions"><button className="expedition-primary" onClick={() => onView("stages")}><Swords size={15} />进入第 {profile?.current_stage || 1} 关</button><button className="expedition-secondary" onClick={() => onView("formation")}><Shield size={15} />编辑九宫格阵容</button></div></div><div className="expedition-cast"><div className="expedition-cast-orbit" />{heroes.slice(0, 3).map((hero, index) => <div className={`expedition-cast-card cast-${index}`} key={hero.hero_id}>{heroPortrait(hero, "large")}<b>{hero.name}</b><small>{hero.role}</small></div>)}</div></section><section className="expedition-panel expedition-slots-panel"><PanelTitle icon={<WandSparkles size={16} />} title="全局训练" action="所有角色共享等级" /><div className="expedition-shared-training"><div className="expedition-shared-training-level"><span>TRAINING LEVEL</span><strong>Lv.{training?.level || 1}</strong><small>上阵角色自动继承，不随位置变化</small></div><div className="expedition-slot-progress"><i style={{ width: `${Math.min(100, ((training?.level || 1) / (training?.max_level || 100)) * 100)}%` }} /></div><button className="expedition-small-button" onClick={onUpgrade} disabled={(profile?.training_exp || 0) < 100 + (training?.level || 1) * 20}>升级全局等级</button></div><p className="expedition-footnote">训练经验来自已验证学习；重置会返还全局已投入经验，不再存在按站位分别培养。</p></section><section className="expedition-panel"><PanelTitle icon={<Crown size={16} />} title="每日学习券" action={`${dailyVerified} 分钟 · 每天 00:00 刷新`} />{milestones.map(item => <div className="expedition-milestone" key={item.milestone_id}><div><b>{item.required_minutes} 分钟</b><span>{item.label}</span></div>{item.claimed ? <span className="expedition-claimed">已兑换</span> : <button className="expedition-small-button" disabled={!item.claimable_count} onClick={() => onClaim(item.milestone_id)}>{item.claimable_count ? `兑换 ${item.claimable_count} 张` : `还差 ${Math.max(0, item.required_minutes - dailyVerified)} 分钟`}</button>}</div>)}<div className="expedition-ticket-wallet"><span>装备十连券 <b>{equipmentTickets}</b></span><span>角色十连券 <b>{characterTickets}</b></span></div></section><section className="expedition-panel"><PanelTitle icon={<Coins size={16} />} title="最近奖励账本" action="只统计验证通过的 Learning Run" />{ledger.length ? ledger.slice(0, 4).map(item => <div className="expedition-ledger-row" key={String(item.ledger_id)}><span>{String(item.learning_run_id || "学习任务")}</span><b>+{String(item.released_minutes || 0)} 分钟</b></div>) : <div className="expedition-empty">完成并通过一个学习场景后，这里会出现可追溯的奖励记录。</div>}</section></div>
}

function PanelTitle({ icon, title, action }: { icon: ReactNode; title: string; action?: string }) { return <div className="expedition-panel-title"><div>{icon}<h3>{title}</h3></div>{action && <span>{action}</span>}</div> }

function Tab({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) {
  return <button className={`expedition-tab ${active ? "active" : ""}`} onClick={onClick}>{icon}<span>{label}</span></button>
}

function Heroes({ heroes, selected, onSelect }: { heroes: Hero[]; selected?: Hero; onSelect: (hero: Hero) => void }) { return <div className="expedition-section-grid"><section className="expedition-panel expedition-hero-list"><PanelTitle icon={<Users size={16} />} title="角色图鉴" action={`${heroes.filter(hero => hero.owned).length} / ${heroes.length} 已获得`} /><div className="expedition-hero-cards">{heroes.map(hero => <button key={hero.hero_id} className={`expedition-hero-card ${hero.owned ? "owned" : "locked"} ${selected?.hero_id === hero.hero_id ? "selected" : ""}`} onClick={() => onSelect(hero)}>{heroPortrait(hero)}<span><b>{hero.name}</b><small>{hero.role} · {hero.rarity}</small><i>{hero.owned ? `★ ${hero.stars}` : "尚未获得"}</i></span></button>)}</div></section><section className="expedition-panel expedition-hero-detail">{selected ? <><div className="expedition-detail-head">{heroPortrait(selected, "large")}<div><span className="expedition-kicker">HERO DETAIL · {selected.rarity.toUpperCase()}</span><h2>{selected.name}</h2><p>{selected.role} · {selected.tags?.join(" / ")}</p></div></div><div className="expedition-skill-grid">{[selected.active_skill, selected.passive_one, selected.passive_two, selected.awakening].filter(Boolean).map(skill => <article key={skill!.skill_id}><img src={resolveExpeditionAsset(selected, "skill_icon").url} alt="" onError={event => { event.currentTarget.src = fallbackAssetUrl(selected.hero_id, "skill_icon") }} /><span>{skill!.name}</span><p>{skill!.summary}</p></article>)}</div><div className="expedition-weakness"><b>设计中的解法空间</b><span>{selected.weakness || "通过站位、护盾、净化、命中与节奏调整应对"}</span></div></> : <div className="expedition-empty">选择一个角色查看技能与克制解法。</div>}</section></div> }

function Formation({ heroes, lineups, draft, setDraft, onSave, onActivate }: { heroes: Hero[]; lineups: Lineup[]; draft: Lineup | null; setDraft: (lineup: Lineup) => void; onSave: () => void; onActivate: (id: string) => void }) {
  const [dragged, setDragged] = useState<string | null>(null)
  const [selectedCell, setSelectedCell] = useState<string | null>(null)
  const heroFor = (id: string) => heroes.find(hero => hero.hero_id === id)
  const place = (cellId: string, heroId: string) => {
    if (!draft) return
    const source = draft.members.find(member => member.hero_id === heroId)
    const target = draft.members.find(member => member.slot_id === cellId)
    if (source && target && source.slot_id !== target.slot_id) {
      setDraft({ ...draft, members: draft.members.map(member => member.slot_id === source.slot_id ? { ...member, slot_id: cellId } : member.slot_id === target.slot_id ? { ...member, slot_id: source.slot_id } : member) })
    } else if (source) {
      setDraft({ ...draft, members: draft.members.map(member => member.slot_id === source.slot_id ? { ...member, slot_id: cellId } : member) })
    } else if (target) {
      setDraft({ ...draft, members: draft.members.map(member => member.slot_id === target.slot_id ? { ...member, hero_id: heroId } : member) })
    }
  }
  const move = (from: string, to: string) => {
    if (!draft || !from || from === to) return
    if (from.startsWith("cell_")) {
      const source = draft.members.find(member => member.slot_id === from)
      if (source) place(to, source.hero_id)
    } else {
      place(to, from)
    }
  }
  return <div className="expedition-section-grid formation-grid"><section className="expedition-panel"><PanelTitle icon={<Shield size={16} />} title="九宫格阵容" action="玩家面向右侧 · 第 1 行第 1 列在最右前方" /><p className="expedition-formation-help">按战斗视角排列：上方是第 1 行，右侧是第 1 列（前排）。站位会影响行动顺序、同排受击和技能范围。</p><div className="expedition-formation-board grid-3x3">{formationCells.map(cell => { const member = draft?.members.find(item => item.slot_id === cell); const hero = heroFor(member?.hero_id || ""); return <button key={cell} className={`expedition-formation-slot ${hero ? "filled" : "empty"} ${selectedCell === cell ? "selected" : ""}`} draggable={Boolean(hero)} onDragStart={() => setDragged(cell)} onDragOver={event => event.preventDefault()} onDrop={() => { if (dragged) move(dragged, cell); setDragged(null) }} onClick={() => { setSelectedCell(cell); if (dragged) { move(dragged, cell); setDragged(null) } }}>{hero ? <>{heroPortrait(hero, "small")}<b>{hero.name}</b></> : <><span>+</span><b>空位</b></>}<small>{cellLabel(cell)}</small></button> })}</div><div className="expedition-stance-row"><label>战术姿态<select value={draft?.stance || "balanced"} onChange={event => draft && setDraft({ ...draft, stance: event.target.value })}><option value="balanced">均衡 · 50/50 行动</option><option value="vanguard">先锋 · 先手与承伤</option><option value="fortify">固守 · 护盾与稳定</option></select></label><button className="expedition-primary" onClick={onSave} disabled={!draft || draft.members.length !== 5}><Save size={15} />保存阵容</button></div></section><section className="expedition-panel"><PanelTitle icon={<Users size={16} />} title="可用角色" action={`${heroes.length} 名`} /><p className="expedition-formation-help">先点一个格子，再点角色即可替换；也可以直接拖拽。</p><div className="expedition-roster-pool">{heroes.map(hero => <button key={hero.hero_id} draggable onDragStart={() => setDragged(hero.hero_id)} onClick={() => { if (selectedCell) place(selectedCell, hero.hero_id) }}>{heroPortrait(hero, "small")}<span>{hero.name}</span></button>)}</div><div className="expedition-lineup-list"><b>已保存阵容</b>{lineups.map(lineup => <button key={lineup.lineup_id} className={lineup.active ? "active" : ""} onClick={() => setDraft(lineup)}><span>{lineup.name}</span><small>{lineup.active ? "主动" : "点击编辑"}</small>{lineup.active && <em>✓</em>}</button>)}</div></section></div>
}

function Gacha({ banners, profile, result, onPull, heroes }: { banners: GachaBanner[]; profile: ExpeditionProfile | null; result: Array<Record<string, unknown>>; onPull: (banner: GachaBanner, count: 1 | 10, currency?: "crystals" | "ticket") => void; heroes: Hero[] }) { return <div className="expedition-section-grid"><section className="expedition-panel"><PanelTitle icon={<Gem size={16} />} title="招募卡池" action={`水晶 ${profile?.crystals || 0} · 角色券 ${profile?.tickets?.character || 0} · 装备券 ${profile?.tickets?.equipment || 0}`} /><div className="expedition-gacha-grid">{banners.map(banner => { const ticketType = banner.banner_type === "character" ? "character" : "equipment"; const ticketCount = profile?.tickets?.[ticketType] || 0; return <article className="expedition-gacha-card" key={banner.banner_id}><div className="expedition-gacha-art"><WandSparkles size={32} /></div><span className="expedition-kicker">{banner.banner_type === "character" ? "HERO SUMMON" : "EQUIPMENT SUMMON"}</span><h3>{banner.name}</h3><p>后台种子决定结果；十连保底稀有度，重复角色转化为碎片。</p><small>传奇保底 {banner.pity_limit} 抽 · 学习券仅能兑换十连</small><div><button className="expedition-secondary" onClick={() => onPull(banner, 1)}>单抽 · {banner.cost_single}</button><button className="expedition-primary" onClick={() => onPull(banner, 10)}>十连 · {banner.cost_ten}</button>{ticketCount > 0 && <button className="expedition-ticket-button" onClick={() => onPull(banner, 10, "ticket")}>使用 1 张{ticketType === "character" ? "角色" : "装备"}十连券 · 剩余 {ticketCount}</button>}</div></article> })}</div></section><section className="expedition-panel"><PanelTitle icon={<Sparkles size={16} />} title="最近结果" action={result.length ? `${result.length} 个结果` : "等待招募"} />{result.length ? <div className="expedition-pull-results">{result.map((item, index) => <div key={`${String(item.hero_id || item.equipment_id)}-${index}`}>{item.type === "hero" && heroPortrait(heroes.find(hero => hero.hero_id === String(item.hero_id)), "small", "avatar")}{item.type === "hero" ? <span className="result-orb hero">✦</span> : <span className="result-orb gear">◆</span>}<b>{String(item.hero_id || item.equipment_id)}</b><small>{String(item.rarity)} · {item.new ? "新角色" : "重复转碎片"}</small></div>)}</div> : <div className="expedition-empty">完成并验证学习场景后，系统会发放每日十连券；水晶则用于普通抽取。</div>}</section></div> }

function Stages({ stage, stageNumber, setStageNumber, onChallenge, busy, battle, events, heroes }: { stage: Stage | null; stageNumber: number; setStageNumber: (n: number) => void; onChallenge: () => void; busy: boolean; battle: { battle_id: string; winner: string; stage_number: number; units: BattleUnit[] } | null; events: BattleEvent[]; heroes: Hero[] }) { return <div className="expedition-stage-page"><section className="expedition-panel expedition-stage-hero"><div><span className="expedition-kicker">INFINITE EXPEDITION · CHAPTER {stage?.chapter || 1}</span><h2>第 {stageNumber} 关 · {stage?.theme || "档案回廊"}</h2><p>{stage?.affix || "每 10 关首领，每 50 关章节机制；没有体力消耗，也没有失败惩罚。"}</p><div className="expedition-stage-meta"><span>敌方编队 {stage?.enemy_ids?.join(" · ") || "等待生成"}</span>{stage?.boss && <b>首领：{stage.boss.name}</b>}</div></div><div className="expedition-stage-controls"><label>跳转查看<input type="number" min={1} value={stageNumber} onChange={event => setStageNumber(Math.max(1, Number(event.target.value) || 1))} /></label><button className="expedition-primary" onClick={onChallenge} disabled={busy}><Swords size={15} />{busy ? "战斗写入中" : "开始挑战"}</button></div></section>{stage?.boss && <section className="expedition-boss-card"><Crown size={20} /><div><b>{stage.boss.name}</b><p>{stage.boss.mechanic}</p><small>可用解法：{stage.boss.strategies?.join(" / ")}</small></div></section>}<section className="expedition-panel expedition-stage-rules"><PanelTitle icon={<Sparkles size={16} />} title="系统规划的无限规则" action="不重置、不排名、不强制付费" /><div className="expedition-rule-grid"><span><b>每 10 关</b>首领模板变化</span><span><b>每 50 关</b>章节机制与新敌人</span><span><b>每 100 关</b>背景池与组合变化</span><span><b>每 500 关</b>里程碑奖励</span></div></section>{battle && <BattleReplay events={events} winner={battle.winner} stageNumber={battle.stage_number} units={battle.units} heroes={heroes} />}</div> }

function Inventory({ equipment, pets }: { equipment: { definitions: Array<Record<string, unknown>>; instances: Array<Record<string, unknown>> }; pets: { definitions: Array<Record<string, unknown>>; owned: Array<Record<string, unknown>> } }) { return <div className="expedition-section-grid"><section className="expedition-panel"><PanelTitle icon={<Box size={16} />} title="装备工坊" action="武器 · 防具 · 核心" /><div className="expedition-inventory-list">{equipment.definitions.map(item => <article key={String(item.equipment_id)}><span className="inventory-icon">◆</span><div><b>{String(item.name)}</b><small>{String(item.slot)} · {String(item.quality)}</small><p>{String(item.effect || "固定主属性，开放效果")}</p></div></article>)}{!equipment.definitions.length && <div className="expedition-empty">装备目录正在等待后端数据。</div>}</div></section><section className="expedition-panel"><PanelTitle icon={<WandSparkles size={16} />} title="宠物图鉴" action={`${pets.owned.length} / ${pets.definitions.length} 已拥有`} /><div className="expedition-inventory-list">{pets.definitions.map(item => <article key={String(item.pet_id)}><span className="inventory-icon pet">✦</span><div><b>{String(item.name)}</b><small>{pets.owned.some(pet => pet.pet_id === item.pet_id) ? "已拥有" : "待解锁"}</small><p>{String(item.passive || "陪伴型被动")}</p></div></article>)}</div></section></div> }

function ArtReview({ heroes, packs, active, onRefresh }: { heroes: Hero[]; packs: ArtPackVersion[]; active: ArtPackVersion | null; onRefresh: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const pack = packs[0]
  const validationAssets = pack?.validation_result?.assets || []
  const report = () => {
    if (!pack) return
    const blob = new Blob([JSON.stringify(pack.validation_result || pack, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = pack.pack_id + "-" + pack.version + "-review.json"
    anchor.click()
    URL.revokeObjectURL(url)
  }
  const exportContactSheet = async () => {
    const cellWidth = 180
    const cellHeight = 150
    const canvas = document.createElement("canvas")
    canvas.width = cellWidth * 4
    canvas.height = cellHeight * 6
    const context = canvas.getContext("2d")
    if (!context) return
    context.fillStyle = "#0b1719"
    context.fillRect(0, 0, canvas.width, canvas.height)
    for (let index = 0; index < heroes.length; index += 1) {
      const hero = heroes[index]
      const asset = resolveExpeditionAsset(hero, "portrait")
      const image = new Image()
      image.src = asset.url
      await new Promise<void>(resolve => { image.onload = () => resolve(); image.onerror = () => resolve() })
      const x = (index % 4) * cellWidth
      const y = Math.floor(index / 4) * cellHeight
      if (image.naturalWidth) context.drawImage(image, x + 8, y + 8, 164, 112)
      context.fillStyle = "#dcebe5"
      context.font = "12px sans-serif"
      context.fillText(hero.name + " · " + hero.hero_id, x + 8, y + 137)
    }
    canvas.toBlob(blob => { if (!blob) return; const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "mentorflow-contact-sheet.png"; anchor.click(); URL.revokeObjectURL(url) }, "image/png")
  }
  const previewValid = preview?.valid === true
  const review = (heroId: string, kind: AssetKind) => pack?.reviews?.find(item => item.hero_id === heroId && item.asset_kind === kind)
  const runPreview = async () => { if (!file) return; try { setBusy(true); setNotice(""); setPreview(await expeditionApi.previewArtPack(file)); } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) } }
  const install = async () => { const previewId = String(preview?.preview_id || ""); if (!previewId) return; try { setBusy(true); await expeditionApi.installArtPack(previewId, true); setNotice("美术包已安装，等待逐项人工审核"); setPreview(null); await onRefresh() } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) } }
  const mark = async (heroId: string, kind: AssetKind, status: "APPROVED" | "NEEDS_REDO") => { if (!pack) return; try { setBusy(true); await expeditionApi.reviewArtAsset(pack.pack_id, pack.version, heroId, kind, status); await onRefresh() } catch (error) { setNotice(errorText(error)) } finally { setBusy(false) } }
  return <div className="expedition-art-review"><section className="expedition-panel"><PanelTitle icon={<Palette size={16} />} title="远征美术审核" action="上游 ZIP · 逐项确认 · 可回滚" /><p className="expedition-footnote">正式包未激活时，所有页面继续使用标记为“开发期基础美术”的 SVG fallback。上传后先预览，再安装；未完成 24 × 4 项审核不能激活。</p><div className="art-review-import"><label><Upload size={15} />选择 Expedition_Art_Pack_V1.zip<input type="file" accept=".zip,application/zip" onChange={event => setFile(event.target.files?.[0] || null)} /></label><button className="expedition-primary" onClick={() => void runPreview()} disabled={!file || busy}>安全预览</button>{preview && <div className={`art-review-preview ${previewValid ? "valid" : "invalid"}`}><b>{previewValid ? "预览通过" : "预览拒绝"}</b><span>{String(preview.pack_id || "—")} · {String(preview.version || "—")} · {String(preview.asset_count || 0)} 项资源</span>{previewValid && <button className="expedition-small-button" onClick={() => void install()} disabled={busy}>确认安装</button>}</div>}</div>{notice && <div className="expedition-notice" role="status">{notice}</div>}</section><section className="expedition-panel"><PanelTitle icon={<CheckCircle2 size={16} />} title="版本状态" action={active ? `Active · ${active.version}` : "当前使用 fallback"} />{packs.length ? <div className="art-pack-version-list">{packs.map(item => <article key={`${item.pack_id}-${item.version}`}><div><b>{item.pack_id} · {item.version}</b><small>{item.status} · 审核 {item.reviewed_assets}/{item.required_reviews}</small></div><div className="art-pack-actions"><button className="expedition-small-button" disabled={item.status !== "VALIDATED" || busy} onClick={() => void expeditionApi.activateArtPack(item.pack_id, item.version).then(onRefresh).catch(error => setNotice(errorText(error)))}>激活</button>{active?.pack_id === item.pack_id && active.version === item.version && <button className="expedition-small-button" disabled={busy} onClick={() => void expeditionApi.rollbackArtPack(item.pack_id, item.version).then(onRefresh).catch(error => setNotice(errorText(error)))}>回滚</button>}</div></article>)}</div> : <div className="expedition-empty">暂无已安装版本。当前所有角色继续使用 SVG fallback。</div>}</section><section className="expedition-panel"><PanelTitle icon={<Users size={16} />} title="24 名角色联系表" action={active ? "正式包/回退状态" : "开发期基础美术"} /><div className="art-contact-sheet">{heroes.map(hero => <article key={hero.hero_id}><div className="art-contact-head">{heroPortrait(hero, "small")}<div><b>{hero.name}</b><small>{hero.hero_id}</small></div></div><div className="art-contact-assets">{(["portrait", "avatar", "skill_cutin", "skill_icon"] as AssetKind[]).map(kind => { const item = review(hero.hero_id, kind); const asset = resolveExpeditionAsset(hero, kind); return <div key={kind} className="art-review-asset"><img src={asset.url} alt={`${hero.name} ${kind}`} onError={event => { event.currentTarget.src = `/assets/expedition/heroes/${hero.hero_id}/${kind}.svg` }} /><span>{kind} · {item?.review_status || (asset.fallback_used ? "FALLBACK" : "ACTIVE")}</span>{pack && <div><button disabled={busy} onClick={() => void mark(hero.hero_id, kind, "APPROVED")}>通过</button><button disabled={busy} onClick={() => void mark(hero.hero_id, kind, "NEEDS_REDO")}>重做</button></div>}</div> })}</div></article>)}</div></section></div>
}

function ArtReviewDiagnostics({ heroes, packs, active }: { heroes: Hero[]; packs: ArtPackVersion[]; active: ArtPackVersion | null }) {
  const pack = packs[0]
  const previous = packs.find(item => item.pack_id !== pack?.pack_id || item.version !== pack?.version)
  const assets = pack?.validation_result?.assets || []
  const metadata = pack?.validation_result?.metadata || {}
  const errors = pack?.validation_result?.errors || []
  const download = (name: string, content: string, type = "application/json") => {
    const url = URL.createObjectURL(new Blob([content], { type }))
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = name
    anchor.click()
    URL.revokeObjectURL(url)
  }
  const downloadBlob = (name: string, blob: Blob) => {
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = name
    anchor.click()
    URL.revokeObjectURL(url)
  }
  const exportContactSheet = async () => {
    const cellWidth = 180
    const cellHeight = 150
    const canvas = document.createElement("canvas")
    canvas.width = cellWidth * 4
    canvas.height = cellHeight * 6
    const context = canvas.getContext("2d")
    if (!context) return
    context.fillStyle = "#0b1719"
    context.fillRect(0, 0, canvas.width, canvas.height)
    for (let index = 0; index < heroes.length; index += 1) {
      const hero = heroes[index]
      const asset = resolveExpeditionAsset(hero, "portrait")
      const image = new Image()
      image.src = asset.url
      await new Promise<void>(resolve => { image.onload = () => resolve(); image.onerror = () => resolve() })
      const x = (index % 4) * cellWidth
      const y = Math.floor(index / 4) * cellHeight
      if (image.naturalWidth) context.drawImage(image, x + 8, y + 8, 164, 112)
      context.fillStyle = "#dcebe5"
      context.font = "12px sans-serif"
      context.fillText(`${hero.name} · ${hero.hero_id}`, x + 8, y + 137)
    }
    canvas.toBlob(blob => { if (blob) downloadBlob("mentorflow-contact-sheet.png", blob) }, "image/png")
  }
  const assetFor = (heroId: string, kind: AssetKind) => assets.find(item => item.hero_id === heroId && item.asset_kind === kind)
  const reviewFor = (heroId: string, kind: AssetKind) => pack?.reviews?.find(item => item.hero_id === heroId && item.asset_kind === kind)
  return <section className="expedition-panel art-review-diagnostics" data-testid="art-review-diagnostics">
    <div className="expedition-panel-title"><div><Palette size={16} /><h3>审核诊断与版本对比</h3></div><span>{active ? `当前 Active · ${active.version}` : "当前使用 SVG fallback"}</span></div>
    <div className="art-review-toolbar"><button className="expedition-small-button" disabled={!pack} onClick={() => pack && download(`${pack.pack_id}-${pack.version}-review.json`, JSON.stringify(pack.validation_result || pack, null, 2))}>下载审核报告 JSON</button><button className="expedition-small-button" disabled={!heroes.length} onClick={() => void exportContactSheet()}>导出 contact sheet PNG</button><span>{previous ? `上一版本：${previous.pack_id} · ${previous.version}` : "上一版本：无"}</span></div>
    {errors.length > 0 && <div className="art-review-errors" role="alert"><b>预览/校验错误</b>{errors.map((item, index) => <span key={`${item.path}-${index}`}>{item.code || "ERROR"} · {item.path || "—"} · {item.error || "校验失败"}</span>)}</div>}
    {pack ? <div className="art-review-table-wrap"><table className="art-review-table"><thead><tr><th>角色 / hero_id</th><th>资源</th><th>尺寸 / MIME / 透明</th><th>SHA256</th><th>来源 / 作者 / 许可</th><th>状态 / 对比</th></tr></thead><tbody>{heroes.map(hero => { const meta = metadata[hero.hero_id] || {}; return <tr key={hero.hero_id}><td><b>{hero.name}</b><code>{hero.hero_id}</code></td><td><div className="art-review-kind-list">{(["portrait", "avatar", "skill_cutin", "skill_icon"] as AssetKind[]).map(kind => { const item = assetFor(hero.hero_id, kind); const review = reviewFor(hero.hero_id, kind); const old = previous?.validation_result?.assets?.find(candidate => candidate.hero_id === hero.hero_id && candidate.asset_kind === kind); const changed = old && item && old.sha256 !== item.sha256; return <span key={kind}><b>{kind}</b><small>{item?.path || "缺失"}</small><em>{review?.review_status || (item ? "PENDING" : "FALLBACK")}</em>{changed && <i>与上一版不同</i>}</span> })}</div></td><td><div className="art-review-kind-list">{(["portrait", "avatar", "skill_cutin", "skill_icon"] as AssetKind[]).map(kind => { const item = assetFor(hero.hero_id, kind); return <span key={kind}>{item ? `${item.width}×${item.height} · ${item.mime} · ${item.has_alpha ? "有透明通道" : "无透明通道"}` : "fallback"}</span> })}</div></td><td><div className="art-review-kind-list">{(["portrait", "avatar", "skill_cutin", "skill_icon"] as AssetKind[]).map(kind => <code key={kind}>{assetFor(hero.hero_id, kind)?.sha256?.slice(0, 16) || "fallback"}</code>)}</div></td><td><small>{meta.source || pack.source || "—"}</small><small>{meta.author || "—"}</small><small>{meta.license || "—"}</small></td><td><b>{active?.pack_id === pack.pack_id && active.version === pack.version ? "正式资源" : "待激活/回退"}</b><small>{previous ? `对比 ${previous.version}` : "首个版本"}</small></td></tr> })}</tbody></table></div> : <div className="expedition-empty">尚无已安装美术包；24 名角色均使用明确标记的开发期基础 SVG fallback。</div>}
  </section>
}

