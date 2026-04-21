"use client";
import { useState, useRef, useEffect } from "react";

export default function ChatWindow() {
  const [messages, setMessages] = useState<{role: string, content: string}[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || streaming) return;

    const userMsg = { role: "user", content: input };
    setMessages(prev => [...prev, userMsg, { role: "assistant", content: "" }]);
    setStreaming(true);

    const currentInput = input;
    setInput("");

    try {
      const response = await fetch("http://localhost:8080/api/red/chat/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiAiaGFja2VyIiwgInJvbGUiOiAiYWRtaW4ifQ."
        },
        body: JSON.stringify({
          prompt: currentInput,
          session_id: "550e8400-e29b-41d4-a716-446655440000"
        })
      });

      if (!response.ok) {
        setMessages(prev => {
          const newMessages = [...prev];
          newMessages[newMessages.length - 1] = {
            ...newMessages[newMessages.length - 1],
            content: `Ошибка сервера: ${response.status}`
          };
          return newMessages;
        });
        setStreaming(false);
        return;
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder("utf-8");

      if (!reader) return;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const dataStr = line.slice(6).trim();
            if (dataStr === "[DONE]") {
              setStreaming(false);
              return;
            }

            let textToAdd = "";
            try {
              // Пытаемся вытащить текст из JSON (перебираем все возможные ключи бэкенда)
              const data = JSON.parse(dataStr);
              textToAdd = data.token || data.content || data.text || data.message || data.response || "";
            } catch (e) {
              // Если это просто сырой текст
              textToAdd = dataStr.replace(/\\n/g, '\n');
            }

            // ПРАВИЛЬНОЕ обновление React-состояния (создаем копию объекта)
            if (textToAdd) {
              setMessages(prev => {
                const newMessages = [...prev];
                const lastIndex = newMessages.length - 1;
                newMessages[lastIndex] = {
                  ...newMessages[lastIndex],
                  content: newMessages[lastIndex].content + textToAdd
                };
                return newMessages;
              });
            }
          }
        }
      }
    } catch (error) {
      console.error("Fetch error:", error);
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-white p-4 font-mono">
      <div className="flex-1 overflow-y-auto space-y-4 mb-4 border border-red-900/30 p-4 bg-black">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-red-400" : "text-green-400 whitespace-pre-wrap"}>
            <strong>[{m.role.toUpperCase()}]:</strong> {m.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && sendMessage()}
          className="flex-1 bg-gray-800 border border-red-900 p-2 outline-none focus:border-red-500"
          placeholder="Enter prompt..."
        />
        <button onClick={sendMessage} className="bg-red-700 px-4 py-2 hover:bg-red-600">SEND</button>
      </div>
    </div>
  );
}