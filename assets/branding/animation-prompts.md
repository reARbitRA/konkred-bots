# Konkred Bots — Qwen Animation Prompt Kit

Ready-to-paste prompts for animating the brand assets in **Qwen (Wan) image-to-video**.

## How to use

1. Open Qwen's video generation (chat.qwen.ai → video, or Wan i2v on Alibaba Cloud / HuggingFace).
2. Upload the static asset as the **first frame** (image-to-video mode — never text-to-video, or the design will drift off-brand).
   - `assets/branding/<bot>/profile.png` → square 1:1 output
   - `assets/branding/<bot>/banner.png` → 16:9 output
3. Paste the matching prompt below, set **~5 seconds**, and keep the output aspect identical to the input.
4. Append the **universal style suffix** and the **negative prompt** (if the tool exposes one).

## Universal style suffix

Append this to every prompt:

```
Dark cinematic 3D style, soft studio lighting, glassmorphism, premium brand
animation, smooth fluid motion, seamless perfect loop, high detail. Camera
locked steady, no shake. Do not add new elements, do not change colors.
```

## Negative prompt (where supported)

```
distorted text, garbled letters, morphing logo, warping shapes, extra objects,
flickering, jitter, blur, watermark, subtitles, color shift, camera shake
```

---

## 1. Voiso — Voice-to-Action (violet / ultramarine)

### Profile picture

```
The glowing audio waveform ripples and flows continuously from left to right.
Pulses of violet energy travel along the luminous trail toward the checklist,
and the three bullet lines light up one after another in a repeating sequence.
The violet radial glow breathes softly, tiny particles drift upward. Camera
locked, emblem stays centered.
```

### Welcome banner

```
The voice-note bubble's play button pulses gently. The audio waveform flows
smoothly rightward into the glass checklist card, and the three bullet lines
illuminate in sequence like tasks being completed. Background violet glows
breathe slowly, particles drift. The wordmark "Voiso" and subtitle stay
perfectly static, sharp and legible.
```

---

## 2. DocuNest — Document AI (amber / honey gold)

### Profile picture

```
A soft scanning light sweeps slowly down the document page, making the golden
text lines shimmer as they are read. The woven amber fibers of the nest sway
subtly, and tiny golden sparkles rise from the page and fade. The warm glow
breathes gently. Camera locked, emblem stays centered.
```

### Welcome banner

```
A golden scanning light sweeps down the document in the nest, text lines
shimmering as they are summarized. The three floating glass chips — quiz
checkmark, flashcards, risk shield — bob gently and glow in turn. Golden
sparkles rise and dissolve. The wordmark "DocuNest" and subtitle stay perfectly
static, sharp and legible.
```

---

## 3. MockMate — Interview & IELTS Coach (emerald / mint)

### Profile picture

```
The two speech bubbles pop in alternately, scaling up softly like a live
conversation. The arc-shaped score dial sweeps upward and glows brighter, then
resets in a smooth loop. The microphone pulses subtly like it is listening,
emerald glow breathing. Camera locked, emblem stays centered.
```

### Welcome banner

```
The two speech bubbles alternate popping in beside the studio microphone,
like a mock interview in progress. The score dial arc sweeps upward with a
glowing trail, and the floating score card lifts slightly and glows. The
microphone ring pulses like an audio level meter. The wordmark "MockMate",
subtitle and score card stay perfectly static, sharp and legible.
```

---

## 4. Hookify — Viral Hooks & Scripts (magenta / electric purple)

### Profile picture

```
The glossy play button beats like a heart with a soft magenta pulse, light
trailing along the fishhook curve. The small heart and flame icons orbit
slowly, and tiny hearts float upward and fade like engagement notifications.
Background magenta glow throbs gently. Camera locked, emblem stays centered.
```

### Welcome banner

```

Small hearts and flame icons float up from the vertical reel card and dissolve
like a viral video racking up likes. A glossy light sheen sweeps across the
play-button fishhook, and the countdown badge pulses once per second. Magenta
and purple glows throb gently. The wordmark "Hookify", subtitle and card text
stay perfectly static, sharp and legible.
```

---

## 5. WhaleSonar — Crypto Sentiment Radar (ocean navy / cyan)

### Profile picture

```
The whale glides slowly forward with a gentle undulating motion while sonar
rings expand outward rhythmically and fade. The candlestick bars beneath rise
slowly with soft cyan glow, then the scene loops seamlessly. Particles drift
like plankton. Camera locked, emblem stays centered.
```

### Welcome banner

```
Sonar rings pulse outward from the whale in a steady rhythm as it glides with
a gentle undulating motion. The candlestick chart rises slowly with glowing
tips, and the bullish gauge chip glows brighter as its arrow tilts slightly
upward. Cyan particles drift through the subtle radar grid. The wordmark
"WhaleSonar", subtitle and gauge text stay perfectly static, sharp and legible.
```

---

## Pro tips

- **Looping:** generate ~5 s, then ask for (or trim to) a seamless loop. Rhythmic
  motions (pulses, rings, sweeps) loop far better than one-off events.
- **Text safety:** banners contain rendered text. The prompts explicitly lock it
  static — if a tool still garbles it, animate the **profile** emblems (text-free)
  and keep banners static, or overlay real text in an editor afterwards.
- **Aspect:** if the tool forces 16:9 for the square profiles, pad the PNG onto a
  matching dark navy background first, animate, then crop the center square back out.
- **Where to use on Telegram:** profile photos are always static, but animated
  assets shine as video welcome messages, /start GIF/MP4 attachments, stickers,
  and channel promo posts.
