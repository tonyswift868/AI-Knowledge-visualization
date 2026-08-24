import { layoutKnowledgeMapGraph, type KnowledgeGraphEdgeLike, type KnowledgeGraphNodeLike } from "./knowledgeMapGraph";

type LayoutRequest = {
  generation: number;
  nodes: KnowledgeGraphNodeLike[];
  edges: KnowledgeGraphEdgeLike[];
};

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const { generation, nodes, edges } = event.data;
  const positions = layoutKnowledgeMapGraph(nodes, edges);
  self.postMessage({ generation, positions });
};

export {};

