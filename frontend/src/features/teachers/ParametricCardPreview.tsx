import type { TeacherAvatarMorph } from "./api"

function deformation(morph: TeacherAvatarMorph, zoom: number) {
  const width = Math.max(.78, Math.min(1.5, .92 + (morph.upper_chest - 1) * .1 + (morph.shoulder_width - 1) * .075 + (morph.waist_width - 1) * .055 + (morph.hip_width - 1) * .09)) * zoom
  const height = Math.max(.86, Math.min(1.24, .96 + (morph.height - 1) * .07 + (morph.leg_length - 1) * .045)) * zoom
  return { transform: `scale(${width.toFixed(3)}, ${height.toFixed(3)})`, transformOrigin: "center 78%", transition: "transform 120ms ease-out" }
}

export function ParametricCardPreview({ src, morph, zoom = 1, alt }: { src: string; morph: TeacherAvatarMorph; zoom?: number; alt: string }) {
  return <div className="parametric-card-preview" aria-label="恋活参数实时预览">
    <img className="parametric-card-frame" src={src} alt={alt} />
    <img className="parametric-card-deformed" src={src} style={deformation(morph, zoom)} alt="" aria-hidden="true" />
  </div>
}
