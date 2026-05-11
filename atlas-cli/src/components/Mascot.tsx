import React, { useState, useEffect, useRef } from "react";
import { Box, Text } from "ink";

export type MascotState = "idle" | "typing" | "thinking" | "happy" | "error";

interface Props {
  state?: MascotState;
}

const TIP_FRAMES  = ["✦", "✧", "·", "✧", "✦"];
const STAR_FRAMES = ["★", "✦", "✧", "·", "✧", "✦"];
const THINK_DOT   = ["·  ", "·· ", "···", "·· "];

const FACE: Record<string, string> = {
  normal:   "◕ω◕",
  blink:    "─ω─",
  typing:   "◔ω◔",
  thinking: "●_●",
  happy:    "^ω^",
  error:    "×ω×",
  wink:     "◕ω─",
};

export function Mascot({ state = "idle" }: Props) {
  const tickRef = useRef(0);
  const posRef  = useRef(3);

  const [tick,  setTick]  = useState(0);
  const [pos,   setPos]   = useState(3);   // 0–6, drives leading spaces
  const [dir,   setDir]   = useState(0);
  const [blink, setBlink] = useState(false);

  // ── Movement tick ──────────────────────────────────────────────────────────
  useEffect(() => {
    const ms = state === "error" ? 70 : state === "typing" ? 100 : 140;

    const id = setInterval(() => {
      const t = ++tickRef.current;
      const prev = posRef.current;
      let next = prev;

      if (state === "idle") {
        next = Math.round(3 + 3 * Math.sin(t * 0.09));
      } else if (state === "typing") {
        next = 3 + (t % 2 === 0 ? 1 : -1);
      } else if (state === "error") {
        const seq = [0, 6, 0, 6, 1, 5, 2, 4, 3];
        next = seq[t % seq.length] ?? 3;
      } else if (state === "happy") {
        next = 3 + (t % 4 < 2 ? 0 : 1);
      } else {
        next = 3;
      }

      posRef.current = next;
      setTick(t);
      setPos(next);
      setDir(next > prev ? 1 : next < prev ? -1 : 0);
    }, ms);

    tickRef.current = 0;
    posRef.current  = 3;
    setPos(3);
    setDir(0);

    return () => clearInterval(id);
  }, [state]);

  // ── Blink (idle only) ──────────────────────────────────────────────────────
  useEffect(() => {
    if (state !== "idle") { setBlink(false); return; }
    let alive = true;
    let t1: ReturnType<typeof setTimeout>;
    let t2: ReturnType<typeof setTimeout>;

    const loop = () => {
      t1 = setTimeout(() => {
        if (!alive) return;
        setBlink(true);
        t2 = setTimeout(() => {
          if (!alive) return;
          setBlink(false);
          loop();
        }, 110);
      }, 2000 + Math.random() * 3000);
    };
    loop();
    return () => { alive = false; clearTimeout(t1); clearTimeout(t2); };
  }, [state]);

  // ── Derived ────────────────────────────────────────────────────────────────
  const tip  = TIP_FRAMES[tick % TIP_FRAMES.length] ?? "✦";
  const star = state === "happy"
    ? (STAR_FRAMES[tick % STAR_FRAMES.length] ?? "★")
    : "★";

  const faceKey =
    state === "typing"    ? "typing"
    : state === "thinking"  ? "thinking"
    : state === "happy"     ? "happy"
    : state === "error"     ? "error"
    : blink                 ? "blink"
    : tick % 55 > 52        ? "wink"
    : "normal";

  const eyes = FACE[faceKey] ?? "◕ω◕";
  const armL = dir === -1 ? "<" : " ";
  const armR = dir ===  1 ? ">" : " ";

  const col =
    state === "error"     ? "red"
    : state === "happy"     ? "greenBright"
    : state === "thinking"  ? "cyan"
    : "blueBright";

  // Text-based horizontal position: leading spaces shift the character
  const pad = " ".repeat(Math.max(0, pos));

  const l1 = `${pad}  ${tip}◆${tip}`;
  const l2 = `${pad}${armL}(${eyes})${armR}`;
  const l3 = state === "thinking"
    ? `${pad}  ${THINK_DOT[tick % THINK_DOT.length] ?? "·  "}`
    : `${pad}  ╘${star}╛`;

  // ── Render — Box wrapper is critical so Ink stacks the 3 lines vertically ──
  return (
    <Box flexDirection="column">
      <Text color={col} bold>{l1}</Text>
      <Text color={col} bold>{l2}</Text>
      <Text color={col} bold>{l3}</Text>
    </Box>
  );
}
