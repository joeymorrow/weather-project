# Philosophy of Buddy (A README for Humans)

Welcome to **Morrow Edge | BEACON**. Buddy is more than just a weather dashboard; he's a conversational companion designed to reflect the indomitable human spirit of your city. 

## The "Buddy" Persona
Buddy operates under a carefully crafted prompt. His tone is akin to a top-tier presidential speechwriter—he understands the weight of the world but chooses optimism and resilience. 

- **Tone:** Charismatic, grounded, and observant. Not overly "robotic," but clearly an AI assistant who loves his job.
- **Sensory Grounding:** Buddy relies heavily on the actual weather context (e.g., he won't hallucinate snow if it's 75°F out).
- **Safety:** We've implemented a Keyword Denylist and a Mood Validator. If the weather gets severe, Buddy knows to tone down the cheer and prioritize safety.

## How to Tweak Buddy's Personality
If you wish to change how Buddy speaks (e.g., make him more professional, or add a specific local catchphrase), you can edit the prompt located in `app.py`:

```python
# Search for "pulse_task" inside app.py
pulse_task = "Task 2 (Pulse): Adopt the persona of a masterful, charismatic writer..."
```
Simply adjust this paragraph to fit your municipality's voice.

## Emergency Override (Beacon Flare Mode)
Cities need a way to take the wheel. By navigating to `http://<your-dashboard-url>/admin`, you can log in using your `ADMIN_PASSWORD` and enable **Beacon Flare Mode**.
This will override Buddy's normal dialogue with your emergency message and pulse the entire dashboard with a color of your choosing (e.g., Blue for a Snow Emergency, Orange for Construction, Red for Missing Persons).

## Accessibility (ADA Compliance)
Government entities are legally required to be accessible. BEACON is built with this in mind:
- **Aria-Live Regions:** Screen readers will automatically announce new weather updates as they refresh.
- **Alt Text:** All images and dynamic elements are tagged for screen readers.
- **High Contrast:** The color palette (cyan on dark) meets WCAG 2.1 standards for readability.

## Graceful Degradation
If the OpenWeatherMap API or Gemini API goes down, the dashboard will not crash. Instead, Buddy will gracefully inform the user: *"I'm having trouble seeing the sky right now, but stay safe!"* 

## The 3D Sandbox World
The 3D sandbox (`/rpg`) is highly optimized for performance, meaning it won't melt older devices. It includes a seasonal color palette and dynamic procedural audio. To add local flavor, low-poly representations of landmarks (like a bridge or local building) can be placed dynamically using Three.js instances!