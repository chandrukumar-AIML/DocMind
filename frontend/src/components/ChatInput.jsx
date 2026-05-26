// frontend/src/components/ChatInput.jsx — Nebula Dark
import { useState, useRef, useCallback, useEffect } from "react";
import PropTypes from "prop-types";

function IconSend() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="22" y1="2" x2="11" y2="13"/>
      <polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  );
}
function IconStop() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="5" y="5" width="14" height="14" rx="2"/>
    </svg>
  );
}

function IconMic({ active }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill={active ? "currentColor" : "none"} stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
      <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
      <line x1="12" y1="19" x2="12" y2="23"/>
      <line x1="8" y1="23" x2="16" y2="23"/>
    </svg>
  );
}

// Web Speech API hook
function useSpeechInput(onResult) {
  const [listening, setListening] = useState(false);
  const recogRef = useRef(null);
  const supported = typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);

  useEffect(() => {
    return () => recogRef.current?.abort();
  }, []);

  const toggle = useCallback(() => {
    if (!supported) return;
    if (listening) {
      recogRef.current?.stop();
      setListening(false);
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const r = new SR();
    r.continuous = false;
    r.interimResults = false;
    r.lang = "en-US";
    r.onresult = (e) => {
      const text = e.results[0][0].transcript;
      onResult(text);
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recogRef.current = r;
    r.start();
    setListening(true);
  }, [listening, supported, onResult]);

  return { listening, toggle, supported };
}

export function ChatInput({ onSubmit, onCancel, isStreaming, disabled, placeholder }) {
  const [value, setValue] = useState("");
  const textareaRef = useRef(null);

  const handleSubmit = useCallback(() => {
    const q = value.trim();
    if (!q || isStreaming) return;
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "24px";
    onSubmit(q);
  }, [value, isStreaming, onSubmit]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  const handleInput = useCallback((e) => {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }, []);

  const canSend = !disabled && value.trim().length > 0 && !isStreaming;

  const { listening, toggle: toggleMic, supported: micSupported } = useSpeechInput(useCallback((text) => {
    setValue(prev => prev ? prev + " " + text : text);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + "px";
    }
  }, []));

  return (
    <div className="chat-input-area">
      <div className="chat-input-wrapper">
        <textarea
          ref={textareaRef}
          className="chat-textarea"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={placeholder || "Ask anything about your documents…"}
          disabled={disabled && !isStreaming}
          rows={1}
          aria-label="Type your question"
          aria-multiline="true"
          style={{ minHeight: 24 }}
        />

        {micSupported && !isStreaming && (
          <button
            className={`chat-mic-btn${listening ? " listening" : ""}`}
            onClick={toggleMic}
            type="button"
            aria-label={listening ? "Stop recording" : "Voice input"}
            title={listening ? "Click to stop" : "Speak your question"}
          >
            <IconMic active={listening} />
          </button>
        )}

        {isStreaming ? (
          <button
            className="chat-cancel-btn"
            onClick={onCancel}
            aria-label="Stop generating"
          >
            <IconStop />
          </button>
        ) : (
          <button
            className="chat-send-btn"
            onClick={handleSubmit}
            disabled={!canSend}
            aria-label="Send message"
          >
            <IconSend />
          </button>
        )}
      </div>

      <div className="chat-input-hint">
        <kbd>Enter</kbd> to send &nbsp;·&nbsp; <kbd>Shift+Enter</kbd> for new line
        &nbsp;·&nbsp; <kbd>Ctrl+K</kbd> to focus
      </div>
    </div>
  );
}

ChatInput.propTypes = {
  onSubmit: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
  isStreaming: PropTypes.bool,
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
};
