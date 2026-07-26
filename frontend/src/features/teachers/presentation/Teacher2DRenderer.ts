import { DragonBonesTeacherAdapter } from "./adapters/DragonBonesTeacherAdapter"
import { ImageTeacherAdapter } from "./adapters/ImageTeacherAdapter"
import { PlaceholderTeacherAdapter } from "./adapters/PlaceholderTeacherAdapter"
import type { Teacher2DRenderer, TeacherRendererKind } from "./teacherPresentationTypes"

export function createTeacher2DRenderer(kind: TeacherRendererKind, host: HTMLElement, callbacks: ConstructorParameters<typeof DragonBonesTeacherAdapter>[0]): Teacher2DRenderer {
  if (kind === "image2d") return new ImageTeacherAdapter({ ...callbacks, host })
  if (kind === "dragonbones2d") return new DragonBonesTeacherAdapter({ ...callbacks, host })
  return new PlaceholderTeacherAdapter()
}
