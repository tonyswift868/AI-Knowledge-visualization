import { create } from "zustand";

export type NarrativeState = {
  activeCampaign: any | null;
  currentScene: any | null;
  dialogueHistory: any[];
  availableChoices: any[];
  learningGate: any | null;
  characterVisualStates: Record<string, any>;
  voicePlayback: { status: "idle" | "loading" | "playing" | "paused" | "failed"; cacheKey?: string; error?: string };
  loadingState: "idle" | "loading" | "saving";
  errorState: string;
  revision: number;
  setCampaign: (campaign: any | null) => void;
  addDialogue: (message: any) => void;
  setVoicePlayback: (value: NarrativeState["voicePlayback"]) => void;
  setLoadingState: (value: NarrativeState["loadingState"]) => void;
  setErrorState: (value: string) => void;
  reset: () => void;
};

const initial = {
  activeCampaign: null,
  currentScene: null,
  dialogueHistory: [],
  availableChoices: [],
  learningGate: null,
  characterVisualStates: {},
  voicePlayback: { status: "idle" as const },
  loadingState: "idle" as const,
  errorState: "",
  revision: 0,
};

export const useNarrativeStore = create<NarrativeState>((set) => ({
  ...initial,
  setCampaign: (campaign) => set({ activeCampaign: campaign, currentScene: campaign?.current_scene || null, availableChoices: campaign?.current_scene?.choices || [], learningGate: campaign?.state?.pending_learning_gate || null, revision: campaign?.revision || 0, errorState: "" }),
  addDialogue: (message) => set((state) => ({ dialogueHistory: [...state.dialogueHistory, message] })),
  setVoicePlayback: (voicePlayback) => set({ voicePlayback }),
  setLoadingState: (loadingState) => set({ loadingState }),
  setErrorState: (errorState) => set({ errorState }),
  reset: () => set(initial),
}));
