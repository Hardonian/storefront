---
version: alpha
name: The Platform Evidence Console
description: Warm operator-grade surfaces with teal proof signals, amber attention states, and restrained motion.
colors:
  primary: "#0F766E"
  primaryHover: "#115E59"
  ink: "#1F2933"
  muted: "#66717D"
  canvas: "#F5F1E8"
  surface: "#FFFDF8"
  border: "#D8D3CA"
  attention: "#B45309"
  success: "#166534"
typography:
  display:
    fontFamily: system-ui
    fontSize: 3.5rem
    fontWeight: 800
    lineHeight: 1.02
    letterSpacing: "-0.06em"
  body:
    fontFamily: system-ui
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.6
rounded:
  sm: 8px
  md: 12px
  lg: 16px
spacing:
  sm: 8px
  md: 16px
  lg: 24px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#FFFFFF"
    rounded: "{rounded.sm}"
    padding: 12px
  button-primary-hover:
    backgroundColor: "{colors.primaryHover}"
    textColor: "#FFFFFF"
  proof-callout:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: 24px
---

## Overview

The storefront is a Decide/Learn surface with an evidence-console posture: clear claims, visible proof, and one dominant next action per section. It is intentionally original and does not clone a vendor interface.

## Colors

Teal represents verified operation and action. Amber represents attention or a planning estimate. Warm canvas/surface tones keep the interface human and readable. Do not use gradients as the primary hierarchy.

## Motion

Microinteractions clarify state: cards lift on hover/focus, images gain a restrained zoom, CTA buttons receive a single directional sheen, and content reveals as it enters the viewport. All non-trivial motion is disabled under `prefers-reduced-motion`.

## Accessibility

Interactive elements retain visible focus rings. Touch behavior does not depend on pointer motion. GET routes are the authoritative browser smoke target; product CTA links must remain keyboard reachable and preserve their target/rel safety attributes.
