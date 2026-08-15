import { useState } from "react";
import { createRoot } from "react-dom/client";

import {
  KnowledgeMapV1Panel,
  normalizeKnowledgeMapV1,
  TeacherTurnV1Renderer,
} from "../src/features/active-teaching/ActiveTeachingChainView";

const map = normalizeKnowledgeMapV1({
  revision: 7,
  status: "ready_with_review",
  nodes: [
    {
      id: "material-node",
      name: "Sentinel Material Concept",
      explanation: "A grounded random-domain explanation.",
      layer: "material",
      status: "confirmed",
      planned: true,
      source_evidence: [{
        source_id: "source-1",
        chunk_id: "chunk-1",
        heading: "Random-domain source",
        quote: "Trusted material excerpt.",
      }],
    },
    {
      id: "common-node",
      name: "Hidden Common Concept",
      explanation: "A model-common suggestion.",
      layer: "model_common",
      status: "pending",
    },
  ],
  edges: [{
    id: "edge-1",
    source_id: "material-node",
    target_id: "common-node",
    relation: "prerequisite",
    status: "pending",
    evidence: [{ source_id: "source-1", chunk_id: "chunk-1", quote: "Edge evidence excerpt." }],
  }],
  candidates: [{
    id: "candidate-1",
    label: "Review this relation",
    status: "pending",
    reason: "Provider relationships require review.",
  }],
});

function Fixture() {
  const [decision, setDecision] = useState("none");
  return (
    <main>
      <KnowledgeMapV1Panel
        map={map}
        capabilities={{ registry: { capabilities: [] } }}
        onGenerate={() => undefined}
        onDecision={(candidate, nextDecision) => setDecision(`${candidate.id}:${nextDecision}`)}
      />
      <output data-testid="browser-decision">{decision}</output>
      <TeacherTurnV1Renderer
        turn={{
          state: { phase: "core_explanation", waiting_for_answer: true },
          concept: "Sentinel Material Concept",
          evidence: "Trusted material excerpt.",
          blocks: [
            { type: "markdown", content: "**安全讲解**<script>window.pwned=true</script><img src=x onerror=alert(1)>" },
            { type: "code", language: "python", code: "print('browser-safe')" },
            { type: "steps", items: ["讲解", "示例", "检查"] },
          ],
          check: { prompt: "请说明它和证据的关系。" },
          expected_learner_action: "提交最小回答",
          plain_text: "安全讲解。",
        }}
      />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
