import { useOffice } from "../store";

export function MissionBoard() {
  const board = useOffice((s) => s.board);
  const unresolved = useOffice((s) => s.unresolvedQuestions);
  if (!board.length) return null;
  return (
    <div className="mission-board">
      <h2>Mission board</h2>
      {board.map((step, i) => (
        <div key={step.id} className={`board-step ${step.state}`}>
          <span className="mark">{step.state === "done" ? "✓" : i + 1}</span>
          <span>{step.label}</span>
        </div>
      ))}
      {unresolved.length > 0 && (
        <>
          <h2 style={{ marginTop: 12 }}>Open questions</h2>
          {unresolved.slice(0, 4).map((q) => (
            <div key={q} className="board-step active" style={{ fontWeight: 400 }}>
              <span className="mark" style={{ border: "none" }}>○</span>
              <span>{q}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
