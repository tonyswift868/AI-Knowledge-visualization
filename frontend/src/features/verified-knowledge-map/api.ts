import type { ConceptDetail, KnowledgeMapEnvelope } from "./types";

async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      detail?: { message?: string };
    } | null;
    throw new Error(payload?.detail?.message || `知识图请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export type VerifiedKnowledgeMapApi = {
  getDemo: (signal?: AbortSignal) => Promise<KnowledgeMapEnvelope>;
  getSnapshot: (
    mapReference: string,
    signal?: AbortSignal,
  ) => Promise<KnowledgeMapEnvelope>;
  getConceptDetail: (
    mapReference: string,
    conceptReference: string,
    signal?: AbortSignal,
  ) => Promise<ConceptDetail>;
};

export const verifiedKnowledgeMapApi: VerifiedKnowledgeMapApi = {
  getDemo: async (signal) => readResponse<KnowledgeMapEnvelope>(
    await fetch("/api/verified-knowledge-map/demo/java-database", { signal }),
  ),
  getSnapshot: async (mapReference, signal) => readResponse<KnowledgeMapEnvelope>(
    await fetch(
      `/api/verified-knowledge-map/maps/${encodeURIComponent(mapReference)}`,
      { signal },
    ),
  ),
  getConceptDetail: async (mapReference, conceptReference, signal) => (
    readResponse<ConceptDetail>(
      await fetch(
        `/api/verified-knowledge-map/maps/${encodeURIComponent(mapReference)}`
        + `/concepts/${encodeURIComponent(conceptReference)}`,
        { signal },
      ),
    )
  ),
};
