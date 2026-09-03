"""AI Chat Interface — Multi-model support, conversation history, and prompt templates.

Features:
- Multiple AI model support with different capabilities
- Conversation management with history
- Prompt templates for common tasks
- Code block highlighting
- Token counting and usage tracking
- Temperature/parameter controls
- System prompt configuration
- Export conversations
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class ModelCapability(Enum):
    CHAT = "chat"
    CODE = "code"
    VISION = "vision"
    REASONING = "reasoning"
    CREATIVE = "creative"
    FAST = "fast"

    @property
    def icon(self) -> str:
        icons = {
            ModelCapability.CHAT: "💬", ModelCapability.CODE: "💻",
            ModelCapability.VISION: "👁", ModelCapability.REASONING: "🧠",
            ModelCapability.CREATIVE: "🎨", ModelCapability.FAST: "⚡",
        }
        return icons.get(self, "?")


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

    @property
    def icon(self) -> str:
        icons = {
            MessageRole.USER: "👤",
            MessageRole.ASSISTANT: "🤖",
            MessageRole.SYSTEM: "⚙️",
        }
        return icons.get(self, "?")


@dataclass
class AIModel:
    name: str = ""
    provider: str = ""
    version: str = ""
    max_tokens: int = 4096
    cost_per_1k: float = 0.0
    capabilities: List[ModelCapability] = field(default_factory=list)
    speed_tokens_per_sec: float = 50.0
    available: bool = True

    @property
    def capability_icons(self) -> str:
        return " ".join(c.icon for c in self.capabilities)

    @property
    def cost_str(self) -> str:
        if self.cost_per_1k == 0:
            return "free"
        return f"${self.cost_per_1k:.3f}/1K"

    @property
    def speed_bar(self) -> str:
        filled = min(20, int(self.speed_tokens_per_sec / 5))
        return "█" * filled + "░" * (20 - filled)


@dataclass
class ChatMessage:
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp: float = 0.0
    model: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    code_blocks: List[str] = field(default_factory=list)

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.timestamp))

    @property
    def token_str(self) -> str:
        if self.tokens_used == 0:
            return ""
        return f"({self.tokens_used} tokens)"

    @property
    def latency_str(self) -> str:
        if self.latency_ms == 0:
            return ""
        if self.latency_ms < 1000:
            return f"{self.latency_ms:.0f}ms"
        return f"{self.latency_ms / 1000:.1f}s"

    @property
    def truncated(self) -> str:
        max_len = 120
        if len(self.content) <= max_len:
            return self.content
        return self.content[:max_len] + "..."


@dataclass
class PromptTemplate:
    name: str = ""
    category: str = ""
    prompt: str = ""
    description: str = ""
    variables: List[str] = field(default_factory=list)
    icon: str = "📝"

    @property
    def preview(self) -> str:
        return self.prompt[:80] + "..." if len(self.prompt) > 80 else self.prompt


@dataclass
class Conversation:
    title: str = ""
    messages: List[ChatMessage] = field(default_factory=list)
    model: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    system_prompt: str = ""
    total_tokens: int = 0

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def age_str(self) -> str:
        age = time.time() - self.created_at
        if age < 3600:
            return f"{age / 60:.0f}m ago"
        if age < 86400:
            return f"{age / 3600:.1f}h ago"
        return f"{age / 86400:.0f}d ago"

    @property
    def last_message_preview(self) -> str:
        if not self.messages:
            return "(empty)"
        return self.messages[-1].truncated


@dataclass
class ChatStats:
    total_messages: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    avg_latency_ms: float = 0.0
    model_usage: Dict[str, int] = field(default_factory=dict)


class AIChat:
    def __init__(self):
        self._models: List[AIModel] = []
        self._conversations: List[Conversation] = []
        self._current_conversation: int = 0
        self._selected_model: int = 0
        self._templates: List[PromptTemplate] = []
        self._system_prompt: str = "You are a helpful AI assistant specializing in operating system development."
        self._temperature: float = 0.7
        self._max_tokens: int = 2048
        self._top_p: float = 0.9
        self._view_mode: str = "chat"  # chat, history, models, templates, stats
        self._stats = ChatStats()
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Models
        self._models = [
            AIModel("Claude 3.5 Sonnet", "Anthropic", "v3.5", 200000, 0.003,
                    [ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.REASONING],
                    speed_tokens_per_sec=80),
            AIModel("GPT-4o", "OpenAI", "4o", 128000, 0.005,
                    [ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.VISION, ModelCapability.REASONING],
                    speed_tokens_per_sec=60),
            AIModel("GPT-4o Mini", "OpenAI", "4o-mini", 128000, 0.00015,
                    [ModelCapability.CHAT, ModelCapability.FAST],
                    speed_tokens_per_sec=120),
            AIModel("Claude 3 Haiku", "Anthropic", "v3", 200000, 0.00025,
                    [ModelCapability.CHAT, ModelCapability.FAST, ModelCapability.CODE],
                    speed_tokens_per_sec=150),
            AIModel("Gemini 1.5 Pro", "Google", "1.5-pro", 2000000, 0.0035,
                    [ModelCapability.CHAT, ModelCapability.VISION, ModelCapability.REASONING, ModelCapability.CREATIVE],
                    speed_tokens_per_sec=70),
            AIModel("Llama 3.1 70B", "Meta", "3.1-70b", 128000, 0.0009,
                    [ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.CREATIVE],
                    speed_tokens_per_sec=40),
            AIModel("DeepSeek Coder V2", "DeepSeek", "v2", 128000, 0.0003,
                    [ModelCapability.CODE, ModelCapability.REASONING],
                    speed_tokens_per_sec=50),
        ]

        # Templates
        self._templates = [
            PromptTemplate("Code Review", "Code", "Review the following code for bugs, security issues, and performance problems. Suggest improvements.", "Review code quality", icon="🔍"),
            PromptTemplate("Debug Help", "Code", "I'm getting this error: {error}. Here's the relevant code:\n```\n{code}\n```\nHelp me fix this.", "Debug an error", icon="🐛"),
            PromptTemplate("Explain Code", "Code", "Explain the following code step by step. What does it do, and how does it work?", "Understand code", icon="📖"),
            PromptTemplate("Write Tests", "Code", "Write comprehensive unit tests for the following code. Cover edge cases and error scenarios.", "Generate tests", icon="🧪"),
            PromptTemplate("Refactor", "Code", "Refactor this code to be more readable, maintainable, and follow best practices.", "Improve code", icon="🔧"),
            PromptTemplate("Summarize", "General", "Summarize the following text in 3-5 key points.", "Summarize content", icon="📝"),
            PromptTemplate("Translate", "General", "Translate the following text to {language}. Preserve the tone and meaning.", "Translate text", icon="🌐"),
            PromptTemplate("Creative Writing", "Creative", "Write a creative short story about {topic}. Use vivid descriptions and engaging narrative.", "Generate story", icon="✍️"),
            PromptTemplate("API Design", "Code", "Design a RESTful API for {feature}. Include endpoints, methods, request/response formats.", "Design API", icon="🌐"),
            PromptTemplate("Architecture", "Design", "Design the architecture for {feature}. Consider scalability, security, and maintainability.", "System design", icon="🏗"),
            PromptTemplate("Git Commit", "Code", "Write a clear, concise git commit message for these changes:\n{diff}", "Write commit message", icon="📝"),
            PromptTemplate("Documentation", "Docs", "Write comprehensive documentation for {function} including parameters, return values, and usage examples.", "Generate docs", icon="📚"),
        ]

        # Sample conversations
        conv1 = Conversation(
            title="Compositor GPU Acceleration",
            model="Claude 3.5 Sonnet",
            created_at=now - 7200,
            updated_at=now - 3600,
            system_prompt=self._system_prompt,
        )
        conv1.messages = [
            ChatMessage(MessageRole.USER, "How should I implement GPU-accelerated compositing in Nyrqis?", now - 7200,
                        tokens_used=15),
            ChatMessage(MessageRole.ASSISTANT, "For GPU-accelerated compositing in Nyrqis, I recommend using Vulkan for the rendering pipeline. Here's the approach:\n\n1. **Surface Management**: Use `VK_KHR_swapchain` for display surfaces\n2. **Layer Composition**: Render each window layer as a separate Vulkan texture\n3. **Efficient Blending**: Use hardware alpha blending with `VK_BLEND_FACTOR_SRC_ALPHA`\n4. **Frame Synchronization**: Use semaphores for GPU-CPU sync\n\n```rust\npub fn composite(layers: &[Layer]) -> Result<Frame, RenderError> {\n    let mut cmd = device.create_command_buffer()?;\n    cmd.begin_render_pass(&render_pass, &framebuffer);\n    for layer in layers {\n        cmd.bind_pipeline(&blend_pipeline);\n        cmd.draw_texture(&layer.texture, layer.position);\n    }\n    cmd.end_render_pass();\n    Ok(cmd.submit()?)\n}\n```\n\nThis gives you sub-millisecond frame times on modern GPUs.",
                        model="Claude 3.5 Sonnet", tokens_used=450, latency_ms=1200, code_blocks=["rust"]),
            ChatMessage(MessageRole.USER, "What about fallback for systems without Vulkan?", now - 3600, tokens_used=20),
            ChatMessage(MessageRole.ASSISTANT, "For Vulkan fallback, implement a cascading renderer:\n\n1. Try Vulkan first\n2. Fall back to EGL/OpenGL ES 3.0\n3. Fall back to software rendering with Pillow\n\nThe `Backend` abstraction layer you've already built handles this perfectly. Just extend it with renderer detection:\n\n```python\ndef get_renderer():\n    if VulkanRenderer.available():\n        return VulkanRenderer()\n    elif EGLRenderer.available():\n        return EGLRenderer()\n    return SoftwareRenderer()\n```",
                        model="Claude 3.5 Sonnet", tokens_used=380, latency_ms=980, code_blocks=["rust", "python"]),
        ]
        conv1.total_tokens = 865

        conv2 = Conversation(
            title="Wayland Bridge Debugging",
            model="GPT-4o",
            created_at=now - 86400,
            updated_at=now - 43200,
            system_prompt=self._system_prompt,
        )
        conv2.messages = [
            ChatMessage(MessageRole.USER, "The Wayland bridge disconnects on suspend. How to fix?", now - 86400,
                        tokens_used=18),
            ChatMessage(MessageRole.ASSISTANT, "This is a common issue. The Wayland connection drops because:\n\n1. The display server releases the compositor on suspend\n2. File descriptors become invalid\n3. The client needs to reconnect on resume\n\nSolution: implement a reconnection handler...", model="GPT-4o",
                        tokens_used=320, latency_ms=800),
        ]
        conv2.total_tokens = 338

        conv3 = Conversation(
            title="Shell UI Architecture Review",
            model="Claude 3.5 Sonnet",
            created_at=now - 86400 * 3,
            updated_at=now - 86400 * 2,
        )
        conv3.messages = [
            ChatMessage(MessageRole.USER, "Review the architecture of the Nyrqis shell UI module system.", now - 86400 * 3, tokens_used=22),
            ChatMessage(MessageRole.ASSISTANT, "The shell architecture is well-designed with clear separation...", model="Claude 3.5 Sonnet",
                        tokens_used=500, latency_ms=1500),
        ]
        conv3.total_tokens = 522

        self._conversations = [conv1, conv2, conv3]

        # Stats
        self._stats = ChatStats(
            total_messages=sum(c.message_count for c in self._conversations),
            total_tokens=sum(c.total_tokens for c in self._conversations),
            total_cost=0.045,
            avg_latency_ms=1100,
            model_usage={"Claude 3.5 Sonnet": 45, "GPT-4o": 30, "GPT-4o Mini": 25},
        )

    @property
    def current_conversation(self) -> Optional[Conversation]:
        if 0 <= self._current_conversation < len(self._conversations):
            return self._conversations[self._current_conversation]
        return None

    @property
    def selected_model(self) -> Optional[AIModel]:
        if 0 <= self._selected_model < len(self._models):
            return self._models[self._selected_model]
        return None

    @property
    def total_conversations(self) -> int:
        return len(self._conversations)

    def select_conversation(self, idx: int):
        if 0 <= idx < len(self._conversations):
            self._current_conversation = idx

    def select_model(self, idx: int):
        if 0 <= idx < len(self._models):
            self._selected_model = idx

    def set_view(self, mode: str):
        if mode in ("chat", "history", "models", "templates", "stats"):
            self._view_mode = mode

    def set_temperature(self, t: float):
        self._temperature = max(0.0, min(2.0, t))

    def set_system_prompt(self, prompt: str):
        self._system_prompt = prompt

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS AI CHAT                                           ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        model = self.selected_model
        conv = self.current_conversation
        model_name = model.name if model else "None"
        lines.append(f"  🤖 {model_name}  🌡 {self._temperature:.1f}  📝 {self._max_tokens} max  💬 {self.total_conversations} conversations  📊 {self._stats.total_tokens} tokens")
        lines.append("")

        if self._view_mode == "chat" and conv:
            lines.append(f"  ── {conv.title} ── ({conv.message_count} messages, {conv.age_str})")
            lines.append("")
            for msg in conv.messages:
                role_icon = msg.role.icon
                model_str = f" ({msg.model})" if msg.model else ""
                token_str = f" {msg.token_str}" if msg.token_str else ""
                latency_str = f" {msg.latency_str}" if msg.latency_str else ""
                lines.append(f"  {role_icon} {msg.role.value}{model_str} {msg.time_str}{token_str}{latency_str}")
                # Show content (truncated per line)
                content = msg.content
                for cline in content.split("\n")[:4]:
                    lines.append(f"    {cline[:72]}")
                if content.count("\n") > 4:
                    lines.append(f"    ... ({content.count(chr(10))} more lines)")
                lines.append("")

        elif self._view_mode == "history":
            lines.append("  ── Conversation History ──")
            for i, c in enumerate(self._conversations):
                sel = "▶" if i == self._current_conversation else " "
                lines.append(f"  {sel} 💬 {c.title}")
                lines.append(f"      {c.model}  {c.message_count} msgs  {c.total_tokens} tokens  {c.age_str}")

        elif self._view_mode == "models":
            lines.append("  ── AI Models ──")
            for i, m in enumerate(self._models):
                sel = "▶" if i == self._selected_model else " "
                avail = "🟢" if m.available else "🔴"
                lines.append(f"  {sel} {avail} {m.name} ({m.provider} {m.version})")
                lines.append(f"      {m.capability_icons}  Cost: {m.cost_str}  Speed: [{m.speed_bar}] {m.speed_tokens_per_sec:.0f} tok/s  Max: {m.max_tokens:,}")

        elif self._view_mode == "templates":
            lines.append("  ── Prompt Templates ──")
            for t in self._templates:
                lines.append(f"  {t.icon} {t.name} [{t.category}]")
                lines.append(f"      {t.description}")
                lines.append(f"      {t.preview}")

        elif self._view_mode == "stats":
            lines.append("  ── Usage Statistics ──")
            lines.append(f"  📊 Total Messages: {self._stats.total_messages}")
            lines.append(f"  📝 Total Tokens: {self._stats.total_tokens:,}")
            lines.append(f"  💰 Total Cost: ${self._stats.total_cost:.3f}")
            lines.append(f"  ⏱ Avg Latency: {self._stats.avg_latency_ms:.0f}ms")
            lines.append("")
            lines.append("  ── Model Usage ──")
            for model_name, count in self._stats.model_usage.items():
                total = sum(self._stats.model_usage.values())
                pct = count / max(1, total) * 100
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                lines.append(f"  {model_name:<22s} [{bar}] {count} ({pct:.0f}%)")

        lines.append("")
        lines.append("  [C]hat [H]istory [M]odels [T]emplates [S]tats [↑↓]Nav [T]emp [N]ew")
        return lines
