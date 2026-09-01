export interface QuestionChangedEvent {
  type: 'question.changed';
  question_id: string;
  status: 'pending' | 'ocr_processing' | 'ready' | 'failed';
  replacement_status: 'pending' | 'processing' | 'failed' | null;
}

type Listener = (event: QuestionChangedEvent) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;

function ensureSource() {
  if (source || typeof window === 'undefined' || typeof EventSource === 'undefined') return;
  source = new EventSource('/api/events/questions');
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as QuestionChangedEvent;
      if (event.type !== 'question.changed' || typeof event.question_id !== 'string') return;
      listeners.forEach((listener) => listener(event));
    } catch {
      // Ignore malformed external events; EventSource keepalives are comments.
    }
  };
  source.onerror = null;
}

export function subscribeToQuestionEvents(listener: Listener): () => void {
  listeners.add(listener);
  ensureSource();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      source?.close();
      source = null;
    }
  };
}

