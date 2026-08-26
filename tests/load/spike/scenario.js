import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("spike");
export { setup };
export default function (data) { runEnterpriseScenario(data, "spike"); }
export function handleSummary(data) { return summary(data, "spike"); }
