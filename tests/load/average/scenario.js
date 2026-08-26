import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("average");
export { setup };
export default function (data) { runEnterpriseScenario(data, "average"); }
export function handleSummary(data) { return summary(data, "average"); }
