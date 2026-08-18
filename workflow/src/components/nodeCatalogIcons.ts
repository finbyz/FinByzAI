import {
  BellRing,
  Calendar,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  GitBranch,
  GitMerge,
  Link2,
  ListTodo,
  MessageSquareText,
  PlusCircle,
  Play,
  RefreshCcw,
  Share2,
  Sparkles,
  Timer,
  Trash2,
  UserRoundPlus,
  Zap,
} from 'lucide-react'
import type { NodeType } from '../types'

const typeIcons: Partial<Record<NodeType, typeof Play>> = {
  'trigger.manual': Play,
  'trigger.document_insert': UserRoundPlus,
  'trigger.document_change': DatabaseZap,
  'trigger.schedule': Calendar,
  'condition.if_else': GitBranch,
  'condition.switch': GitMerge,
  'condition.deduplicate': Share2,
  'delay.fixed': Clock3,
  'delay.until_date': Clock3,
  'delay.until_event': Timer,
  'delay.business_hours': Calendar,
  'transform.value': Sparkles,
  'transform.associated_record': Link2,
  'transform.child_records': ListTodo,
  'action.update_record': DatabaseZap,
  'action.create_record': UserRoundPlus,
  'action.create_todo': ListTodo,
  'action.add_comment': MessageSquareText,
  'action.notify_user': BellRing,
  'action.send_email': BellRing,
  'action.send_sms': MessageSquareText,
  'action.webhook': Zap,
  'action.call_subflow': RefreshCcw,
  'action.numeric_adjust': PlusCircle,
  'action.manage_association': Link2,
  'action.round_robin': Share2,
  'action.delete_record': Trash2,
  'end.complete': CheckCircle2,
}

// The node catalog is delivered by the backend and can be newer than a cached
// frontend bundle during a rolling deploy. React treats an unknown node type as
// an undefined component unless the runtime lookup has a safe fallback.
export function resolveNodeTypeIcon(type: string): typeof Play {
  return typeIcons[type as NodeType] || Zap
}
