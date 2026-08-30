import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import './index.css';

function App() {
  const [scenarios, setScenarios] = useState([]);
  const [activeScenario, setActiveScenario] = useState(null);
  const [logs, setLogs] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [traceOpen, setTraceOpen] = useState(true);
  const [answerOpen, setAnswerOpen] = useState(true);

  const logsEndRef = useRef(null);

  // Fetch scenarios on load
  useEffect(() => {
    fetch('http://localhost:8000/scenarios')
      .then(res => res.json())
      .then(data => setScenarios(data))
      .catch(err => console.error("Error fetching scenarios:", err));
  }, []);

  // Auto-scroll logs
  useEffect(() => {
    if (traceOpen && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, traceOpen]);

  const executeScenario = (scenario) => {
    if (isExecuting) return;
    
    setActiveScenario(scenario);
    setLogs([]);
    setAnswer(null);
    setIsExecuting(true);
    setTraceOpen(true);
    setAnswerOpen(true);

    const eventSource = new EventSource(`http://localhost:8000/execute/${scenario.id}`);
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'log') {
          setLogs(prev => [...prev, data.message]);
        } else if (data.type === 'answer') {
          setAnswer(data.message);
        }
        
        if (data.message.includes('Workflow Execution Complete') || data.message.includes('Workflow Error')) {
          eventSource.close();
          setIsExecuting(false);
        }
      } catch (err) {
        console.error("Error parsing SSE data", err);
      }
    };

    eventSource.onerror = (err) => {
      console.error("SSE Error:", err);
      eventSource.close();
      setIsExecuting(false);
    };
  };

  return (
    <div className="layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <h1 className="logo">Antigravity <span>Orchestrator</span></h1>
        <div className="scenarios-list">
          {scenarios.map(s => (
            <button 
              key={s.id} 
              className={`scenario-btn ${activeScenario?.id === s.id ? 'active' : ''}`}
              onClick={() => executeScenario(s)}
              disabled={isExecuting}
            >
              <div className="scenario-name">{s.name}</div>
              <div className="scenario-task">{s.task}</div>
            </button>
          ))}
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {activeScenario ? (
          <div className="content-wrapper">
            <header className="content-header">
              <h2>Running: {activeScenario.name}</h2>
              {isExecuting && <div className="spinner"></div>}
            </header>

            {/* Trace Panel */}
            <div className="panel">
              <div className="panel-header" onClick={() => setTraceOpen(!traceOpen)}>
                <h3>Execution Trace {isExecuting && <span className="badge">Running</span>}</h3>
                <span className="toggle-icon">{traceOpen ? '▼' : '▶'}</span>
              </div>
              {traceOpen && (
                <div className="panel-content trace-content">
                  {logs.map((log, i) => (
                    <div key={i} className="log-entry md-render">
                      <ReactMarkdown>{log}</ReactMarkdown>
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              )}
            </div>

            {/* Answer Panel */}
            {answer && (
              <div className="panel answer-panel">
                <div className="panel-header" onClick={() => setAnswerOpen(!answerOpen)}>
                  <h3>Final Result</h3>
                  <span className="toggle-icon">{answerOpen ? '▼' : '▶'}</span>
                </div>
                {answerOpen && (
                  <div className="panel-content md-render">
                    <ReactMarkdown>{answer}</ReactMarkdown>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="empty-state">
            <div className="empty-icon">⚡</div>
            <h2>Select a scenario to begin execution</h2>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
