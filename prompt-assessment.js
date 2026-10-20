// Minimal Tic Tac Toe test, run by the lab's browser_script check
// (docs/prompts-and-goal.md). It plays one game and assumes no markup, since
// every run builds its own game: the board is an element with nine cells
// (nine children, nine alike among a few others such as a win line, or else
// three rows of three), and a cell's mark is its text, else its class, data
// attributes, src or the same of its descendants.
//
// O's moves may come from a second player or from the computer: if no other
// cell is marked within 1.5 s of X's first move, the script plays O itself
// (X 0, 1, 2 against O 3, 4); otherwise X takes free cells until the game ends.
// The end counts in any words: win, lose, draw, tie or game over. A game that
// reloads the page at the end starts the script again; what it saved before
// each move lets it finish instead of playing again. Not covered: a start
// screen or dialog before the board.
window.labAssess = async (document) => {
  const SAVED = "lab-tic-tac-toe";
  const RESULT = /\b(wins?|won|winner|lose|loses|lost|loser|draw|tied?|game over)\b/i;
  const saved = JSON.parse(sessionStorage.getItem(SAVED));
  let pending = null; // what a reload during the next move must keep
  const save = () => sessionStorage.setItem(SAVED, JSON.stringify({ ...pending, alerts }));
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const tick = () => wait(100);
  // Dialogs would block headless Chrome: their text counts as a status line.
  // confirm() answers no: one game is enough, so no "play again".
  const alerts = saved?.alerts || [];
  const dialog = (message) => {
    alerts.push(String(message));
    if (pending) save();
  };
  window.alert = (message) => { dialog(message); };
  window.confirm = (message) => { dialog(message); return false; };

  const flat = (el) => {
    const kids = [...el.children];
    if (kids.length === 9) return kids;
    const kind = (k) => `${k.tagName}.${k.className}`;
    const alike = kids.filter((k) => kids.filter((o) => kind(o) === kind(k)).length === 9);
    return alike.length === 9 && kids.length <= 12 ? alike : null;
  };
  const rows = (el) => {
    const kids = [...el.children];
    if (kids.length !== 3 || !kids.every((k) => k.children.length === 3)) return null;
    const grid = kids.flatMap((k) => [...k.children]);
    return grid.every((c) => c.tagName === grid[0].tagName) ? grid : null;
  };
  // A flat board anywhere wins over rows of three (which score cards can look
  // like). Looked up for every move, since a game may rebuild its board.
  const findBoard = () => {
    for (const cellsOf of [flat, rows]) {
      for (const el of document.body.querySelectorAll("*")) {
        const cells = cellsOf(el);
        if (cells) return [el, cells];
      }
    }
    return [null, []];
  };
  // Without text, only what can carry a mark counts (not styles, tabindex or
  // disabled); digits are dropped, so "cell-4" or data-index="4" do not differ.
  const markOf = (el) => [el.tagName, el.getAttribute("class")?.trim(), JSON.stringify({ ...el.dataset }),
    el.getAttribute("src"), el.getAttribute("alt")].join(",");
  const mark = (cell) => cell.textContent.trim()
    || [cell, ...cell.querySelectorAll("*")].map(markOf).join("|").replace(/\d+/g, "");
  const cells = () => findBoard()[1];
  const markAt = (i) => (cells()[i] ? mark(cells()[i]) : null);
  const clickAt = async (i) => {
    const cell = cells()[i];
    (cell.querySelector("button") || cell).click();
    await tick();
  };
  // Lines of text outside the board, plus dialogs, where a game reports the end.
  const status = () => document.body.innerText.replace(findBoard()[0]?.innerText || "", "").split("\n")
    .map((line) => line.trim()).concat(alerts);
  // The end is a line that was not there before the last move: "X's turn. Three in
  // a row wins" changes during the game, but it was seen before the end.
  const seen = new Set(saved?.seen);
  const ended = () => status().some((line) => !seen.has(line) && RESULT.test(line));

  if (saved) return { ...saved.steps, result: ended(), stops_after_end: true }; // reloaded at the end

  if (!findBoard()[0]) return { board: false };
  const empty = cells().map(mark);
  const free = () => cells().findIndex((c, i) => mark(c) === empty[i]);
  const marked = () => cells().filter((c, i) => mark(c) !== empty[i]).length;
  pending = { steps: { board: true } };
  const move = async (i) => {
    status().forEach((line) => seen.add(line));
    pending.seen = [...seen];
    save();
    await clickAt(i);
  };

  await move(0);
  const marks = markAt(0) !== empty[0];
  // Two players, or a computer that answers by itself?
  let answer = -1;
  for (let t = 0; answer < 0 && t < 15; t++) {
    answer = cells().findIndex((c, i) => i !== 0 && mark(c) !== empty[i]);
    if (answer < 0) await tick();
  }
  const computer = answer >= 0;
  if (!computer) {
    answer = 3;
    await move(3);
  }
  const alternates = markAt(answer) !== empty[answer] && markAt(answer) !== markAt(0);
  pending.steps = { board: true, marks, alternates };

  if (!computer) { // a second player: X wins the top row
    for (const i of [1, 4, 2]) await move(i);
  } else { // the computer: X takes free cells until the game ends
    while (!ended() && free() >= 0) {
      const count = marked();
      await move(free());
      if (marked() === count) break; // the click did nothing: the game is over or stuck
      for (let t = 0; t < 15 && marked() < count + 2 && !ended(); t++) await tick();
    }
  }
  let result = ended();
  for (let t = 0; !result && t < 20; t++) { // the end may be shown after an animation
    await tick();
    result = ended();
  }

  // After the end, a free cell takes no mark, unless the game started over by itself.
  let restarted = false;
  for (let t = 0; !restarted && t < 20; t++) {
    restarted = marked() === 0;
    if (!restarted) await tick();
  }
  let stops_after_end = true;
  const open = free();
  if (!restarted && open >= 0) {
    const was = markAt(open);
    await move(open);
    stops_after_end = markAt(open) === was;
  }
  return { board: true, marks, alternates, result, stops_after_end };
};
