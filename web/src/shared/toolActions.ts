import contract from '../../../contracts/tool-actions.json' with { type: 'json' }

export interface ToolActionDefinition {
  id: string
  zh: string
  aliases: string[]
  toolTypes: string[]
}

if (contract.version !== 1) throw new Error('工具动作契约版本不兼容')
export const toolActions: readonly ToolActionDefinition[] = Object.freeze(contract.actions)
export const programToolActions = toolActions.map(({id, zh, aliases}) => ({en:id.toUpperCase(), zh, aliases}))
