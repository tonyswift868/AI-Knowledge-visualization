import type { RouteObject } from "react-router-dom";

import { VerifiedKnowledgeMapExperience } from "./VerifiedKnowledgeMapExperience";

export const VERIFIED_KNOWLEDGE_MAP_ROUTE_PATH = "/verified-knowledge-map";

export const verifiedKnowledgeMapFeatureRoute: RouteObject = {
  path: VERIFIED_KNOWLEDGE_MAP_ROUTE_PATH,
  element: <VerifiedKnowledgeMapExperience />,
};

export function VerifiedKnowledgeMapFeatureRoute() {
  return <VerifiedKnowledgeMapExperience />;
}
