// Minimal Tic Tac Toe test, run by the lab's browser_script check
// (docs/prompts-and-goal.md). It assumes no markup: the board is the element
// with exactly nine child elements, and a cell's mark is its text, else its
// class. X plays 0, 1, 2 and O plays 3, 4, so X wins the top row.
// A restart that reloads the page starts the script again; the results
// saved before the restart let it finish instead of playing again.
window.labAssess = async (document) => {
  const SAVED = "lab-tic-tac-toe";
  const tick = () => new Promise((resolve) => setTimeout(resolve, 100));
  const findBoard = () => [...document.body.querySelectorAll("*")].find((el) => el.children.length === 9);
  const mark = (i, from) => from[i].textContent.trim() || from[i].className;
  const isEmpty = (from, empty) => from.length === 9 && from.every((_, i) => mark(i, from) === empty[i]);

  const saved = JSON.parse(sessionStorage.getItem(SAVED));
  if (saved) {
    const after = [...(findBoard()?.children || [])];
    return { ...saved.steps, restarts: isEmpty(after, saved.empty) };
  }

  const board = findBoard();
  if (!board) return { board: false };
  const cells = [...board.children];
  const click = async (i) => {
    (cells[i].querySelector("button") || cells[i]).click();
    await tick();
  };
  // Lines of text outside the board, where a game reports the result.
  const status = () => document.body.innerText.replace(board.innerText, "").split("\n").map((l) => l.trim());
  const at = (i) => mark(i, cells);
  const empty = cells.map((_, i) => at(i));

  await click(0);
  const marks = at(0) !== empty[0];
  await click(3);
  const alternates = at(3) !== empty[3] && at(3) !== at(0);
  await click(1);
  await click(4);
  const before = status();
  await click(2);
  // A new line that names X's mark (its text, else "X") and a win.
  const x = (cells[0].textContent.trim() || "X").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const win = status().some((line) => !before.includes(line)
    && new RegExp(`(^|[^\\w])${x}([^\\w]|$)`, "i").test(line) && /\b(wins?|won|winner)\b/i.test(line));
  await click(8);
  const stops_after_win = at(8) === empty[8];
  const steps = { board: true, marks, alternates, win, stops_after_win };
  const restart = [...document.querySelectorAll("button")]
    .find((b) => /restart|reset|new game|play again/i.test(b.textContent));
  if (!restart) return { ...steps, restarts: false };
  sessionStorage.setItem(SAVED, JSON.stringify({ steps, empty }));
  restart.click();
  await tick();
  // A game may rebuild its board on restart, so look it up again.
  return { ...steps, restarts: isEmpty([...(findBoard() || board).children], empty) };
};
