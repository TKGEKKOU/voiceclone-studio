from __future__ import annotations

import json
import urllib.error
import urllib.request


class OpenAICompatibleTranslator:
    SYSTEM_PROMPT = (
        "把用户提供的中文转换为自然日语，保留语气、称呼和情绪。"
        "只输出日语译文，不回答原文问题，不添加标题、引号或解释。"
    )

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def translate_ja(self, text: str) -> str:
        if not self.configured:
            raise RuntimeError("请先配置 LLM API，或直接输入日语试听文本")
        body = json.dumps({
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            translated = str(payload["choices"][0]["message"]["content"]).strip().strip("`\"'“”‘’ ")
        except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"LLM 翻译失败：{exc}") from exc
        if not translated:
            raise RuntimeError("LLM 未返回有效日语译文")
        return translated
