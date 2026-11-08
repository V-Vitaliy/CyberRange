import type { AgentStep, ChatMessage } from '../../api/chat';

interface ChatMessagesProps {
  messages: ChatMessage[];
  step: AgentStep | null;
  stepLabel: string;
}

// Model output and tool names are untrusted: React renders them as escaped text only.
export default function ChatMessages({ messages, step, stepLabel }: ChatMessagesProps) {
  return (
    <div className="flex flex-col gap-4">
      {messages.map((message, index) => (
        <div
          key={index}
          className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap break-words ${
            message.role === 'user' ? 'self-end bg-red-600/20 text-white' : 'self-start bg-neutral-900 border border-neutral-800'
          }`}
        >
          {message.content}
        </div>
      ))}
      {step && (
        <p role="status" className="self-start text-xs font-mono text-red-400 animate-pulse">
          {stepLabel} {step.step}: {step.tool}
        </p>
      )}
    </div>
  );
}
