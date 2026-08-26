import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("soak");
export { setup };
export default function (data) { runEnterpriseScenario(data, "soak"); }
export function handleSummary(data) { return summary(data, "soak"); }
