import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { agentChat, decidePurchaseOrder } from '../api/client';
import {
  AgentChatResponse,
  AgentMessage,
  AgentToolCall,
} from '../api/types';
import { Badge, Button, Spinner } from './ui';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  response?: AgentChatResponse | null;
}

const SUGGESTED_PROMPTS = [
  'Give me a daily operations briefing',
  'Which products should I replenish this week?',
  'Compare suppliers for my best-selling items',
  'Why is a purchase order blocked? Show me the policy',
  'Draft a purchase order for low-stock items',
  'Explain my weekly spend envelope status',
];

let msgSeq = 0;
const nextId = () => `m${++msgSeq}`;

function asNumber(v: unknown): number | null {
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    const n = parseFloat(v.replace(/[$,]/g, ''));
    return Number.isNaN(n) ? null : n;
  }
  return null;
}

function extractMetrics(response: AgentChatResponse): Array<{ label: string; value: string }> {
  const metrics: Array<{ label: string; value: string }> = [];
  const candidates: Array<[string, unknown]> = [];
  for (const call of response.tool_calls) {
    if (call.result && typeof call.result === 'object') {
      const rec = call.result as Record<string, unknown>;
      for (const [k, v] of Object.entries(rec)) {
        if (typeof v === 'string' || typeof v === 'number') {
          candidates.push([k, v]);
        }
      }
    }
  }
  const preferred = ['available_spend', 'committed_spend', 'weekly_cap', 'low_stock_count', 'total_cost', 'landed_cost', 'lead_time_days', 'reliability_score'];
  const order: Record<string, number> = {};
  preferred.forEach((p, i) => (order[p] = i));
  candidates.sort((a, b) => {
    const ia = order[a[0]] ?? 99;
    const ib = order[b[0]] ?? 99;
    return ia - ib;
  });
  for (const [key, val] of candidates.slice(0, 4)) {
    const n = asNumber(val);
    const label = key.replace(/_/g, ' ');
    if (n !== null) {
      const isMoney = /cost|spend|price|cap|ceiling/.test(key);
      metrics.push({
        label,
        value: isMoney ? `$${n.toFixed(2)}` : String(n),
      });
    } else if (typeof val === 'string') {
      metrics.push({ label, value: val });
    }
  }
  return metrics;
}

function renderAnswer(text: string): ReactNode[] {
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let inFence = false;
  let fence: string[] = [];

  const flushParagraph = () => {
    if (para.length) {
      blocks.push(<p key={blocks.length} className="oa-answer__p">{para.join(' ')}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={blocks.length} className="oa-answer__list">
          {list.map((li, i) => (
            <li key={i}>{li}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  const lines = text.split('\n');
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.startsWith('```')) {
      flushParagraph();
      flushList();
      if (!inFence) {
        inFence = true;
        fence = [];
      } else {
        inFence = false;
        blocks.push(
          <pre key={blocks.length} className="oa-answer__code">
            <code>{fence.join('\n')}</code>
          </pre>,
        );
      }
      continue;
    }
    if (inFence) {
      fence.push(line);
      continue;
    }
    if (/^#{1,6}\s+/.test(line)) {
      flushParagraph();
      flushList();
      const level = line.match(/^#+/)![0].length;
      const Tag = level <= 2 ? 'h4' : 'h5';
      blocks.push(
        <Tag key={blocks.length} className="oa-answer__heading">{line.replace(/^#+\s+/, '')}</Tag>,
      );
      continue;
    }
    if (/^[-*•]\s+/.test(line)) {
      flushParagraph();
      list.push(line.replace(/^[-*•]\s+/, ''));
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push(
        <p key={blocks.length} className="oa-answer__p">{line}</p>,
      );
      continue;
    }
    if (line.trim() === '') {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    para.push(line);
  }
  flushParagraph();
  flushList();
  if (fence.length) {
    blocks.push(
      <pre key={blocks.length} className="oa-answer__code"><code>{fence.join('\n')}</code></pre>,
    );
  }
  return blocks;
}

function renderToolResult(call: AgentToolCall): ReactNode {
  const r = call.result as Record<string, unknown> | null | undefined;
  if (!r || typeof r !== 'object') {
    return <div className="oa-details__empty">No structured data returned.</div>;
  }
  if (Array.isArray(r.items)) {
    const rows = r.items.slice(0, 8) as Array<Record<string, unknown>>;
    return (
      <div className="oa-table-wrap">
        <table className="oa-table">
          <thead>
            <tr>
              {rows.length
                ? Object.keys(rows[0]).slice(0, 5).map((k) => <th key={k}>{k.replace(/_/g, ' ')}</th>)
                : <th>items</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {Object.values(row).slice(0, 5).map((v, j) => (
                  <td key={j}>{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '—')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div className="oa-kv">
      {Object.entries(r).slice(0, 10).map(([k, v]) => (
        <div className="oa-kv__row" key={k}>
          <span className="oa-kv__k">{k.replace(/_/g, ' ')}</span>
          <span className="oa-kv__v">
            {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '—')}
          </span>
        </div>
      ))}
    </div>
  );
}

function SupportingDetails({ response }: { response: AgentChatResponse }) {
  if (!response.tool_calls.length && !response.blocked.length) return null;
  return (
    <div className="oa-details-list">
      {response.tool_calls.map((call, i) => (
        <details key={i} className="oa-details">
          <summary>
            <span className="oa-details__title">
              {call.name.replace(/_/g, ' ')}
            </span>
            {call.error ? (
              <Badge tone="danger">error</Badge>
            ) : (
              <Badge tone="success">ok</Badge>
            )}
          </summary>
          <div className="oa-details__body">{renderToolResult(call)}</div>
        </details>
      ))}
      {response.blocked.map((b, i) => (
        <div key={`b${i}`} className="oa-note oa-note--warn">
          <strong>Blocked: {b.name.replace(/_/g, ' ')}</strong>
          <span>{b.reason}</span>
        </div>
      ))}
    </div>
  );
}

interface ApprovalPanelProps {
  response: AgentChatResponse;
  onDecided: (msgId: string, outcome: 'approved' | 'rejected') => void;
  msgId: string;
}

function ApprovalPanel({ response, onDecided, msgId }: ApprovalPanelProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: 'approved' | 'rejected') {
    setBusy(true);
    setError(null);
    try {
      const ref = response.approval_reference || response.draft_reference || '';
      await decidePurchaseOrder(ref, {
        decision,
        decision_reason: decision === 'approved' ? 'Owner approved via AI Command Centre' : 'Owner rejected via AI Command Centre',
        idempotency_key: `ai-${ref}-${Date.now()}`,
      });
      onDecided(msgId, decision);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Decision failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="oa-approval">
      <div className="oa-approval__head">
        <span className="oa-approval__badge">Owner approval required</span>
        <span className="oa-approval__ref">
          {response.approval_reference || response.draft_reference || 'draft'}
        </span>
      </div>
      <p className="oa-approval__desc">
        The AI prepared this action but cannot approve it. Review and decide deliberately.
      </p>
      {error && <div className="oa-approval__error">{error}</div>}
      <div className="oa-approval__actions">
        <Button variant="danger" size="sm" disabled={busy} onClick={() => decide('rejected')}>
          {busy ? '…' : 'Reject'}
        </Button>
        <Button variant="primary" size="sm" disabled={busy} onClick={() => decide('approved')}>
          {busy ? 'Submitting…' : 'Approve'}
        </Button>
      </div>
    </div>
  );
}

function MetricsStrip({ metrics }: { metrics: Array<{ label: string; value: string }> }) {
  if (!metrics.length) return null;
  return (
    <div className="oa-metrics">
      {metrics.map((m, i) => (
        <div key={i} className="oa-metric">
          <span className="oa-metric__label">{m.label}</span>
          <span className="oa-metric__value">{m.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Full-page AI workspace: conversation + composer, single coherent scroll. */
export function AICommandCentre() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function sendMessage(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text || sending) return;
    setInput('');
    setError(null);

    const userMsg: ChatMessage = { id: nextId(), role: 'user', content: text };
    setMessages((m) => [...m, userMsg]);
    setSending(true);

    const history: AgentMessage[] = messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }));

    try {
      const resp = await agentChat({ message: text, history });
      setMessages((m) => [
        ...m,
        { id: nextId(), role: 'assistant', content: resp.answer, response: resp },
      ]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: nextId(),
          role: 'assistant',
          content: err instanceof Error ? err.message : 'The agent could not respond.',
          response: null,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  function handleDecided(msgId: string, outcome: 'approved' | 'rejected') {
    setMessages((m) =>
      m.map((msg) =>
        msg.id === msgId && msg.response
          ? {
              ...msg,
              content:
                outcome === 'approved'
                  ? `${msg.content}\n\n✅ Owner approved this purchase order. Spend has been reserved.`
                  : `${msg.content}\n\n⛔ Owner rejected this purchase order.`,
            }
          : msg,
      ),
    );
  }

  const empty = messages.length === 0;

  return (
    <div className="oa-ai">
      <div className="oa-ai__head">
        <div>
          <h3>AI Command Centre</h3>
          <p className="oa-ai__subtitle">Analyse, recommend and draft — approval stays with you.</p>
        </div>
      </div>

      <div className="oa-ai__conv" ref={scrollRef}>
        {empty ? (
          <div className="oa-ai__empty">
            <div className="oa-ai__empty-icon">✦</div>
            <h3>What should we look at?</h3>
            <p>Ask about inventory, suppliers, spend policy, purchase orders or a full briefing.</p>
            <div className="oa-suggestions">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  className="oa-suggestion"
                  onClick={() => sendMessage(prompt)}
                  disabled={sending}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="oa-ai__messages">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                msg={msg}
                onDecided={handleDecided}
                sending={sending}
              />
            ))}
            {sending && (
              <div className="oa-msg oa-msg--assistant">
                <div className="oa-msg__bubble">
                  <Spinner label="Analysing…" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="oa-ai__composer">
        {error && <div className="oa-login__error">{error}</div>}
        <form
          className="oa-composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            sendMessage();
          }}
        >
          <textarea
            ref={inputRef}
            className="oa-composer__input"
            rows={2}
            placeholder="Ask about your operations… (e.g. “which products need replenishment?”)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
          />
          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="oa-composer__send"
            disabled={!input.trim() || sending}
            loading={sending}
          >
            Send
          </Button>
        </form>
        <p className="oa-composer__hint">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  );
}

function MessageBubble({
  msg,
  onDecided,
  sending,
}: {
  msg: ChatMessage;
  onDecided: (msgId: string, outcome: 'approved' | 'rejected') => void;
  sending: boolean;
}) {
  if (msg.role === 'user') {
    return (
      <div className="oa-msg oa-msg--user">
        <div className="oa-msg__bubble oa-msg__bubble--user">{msg.content}</div>
      </div>
    );
  }

  const resp = msg.response;
  return (
    <div className="oa-msg oa-msg--assistant">
      <div className="oa-msg__avatar" aria-hidden="true">✦</div>
      <div className="oa-msg__bubble">
        {resp && <MetricsStrip metrics={extractMetrics(resp)} />}
        <div className="oa-answer">{renderAnswer(msg.content)}</div>
        {resp && <SupportingDetails response={resp} />}
        {resp?.approval_required && !sending && (
          <ApprovalPanel response={resp} onDecided={onDecided} msgId={msg.id} />
        )}
      </div>
    </div>
  );
}
