// Observation direction only: never changes robot pose or task workspace.
export const FRONT_TASK_THETA = Math.PI
export function frontTaskView(view) { return {...view,theta:FRONT_TASK_THETA} }
