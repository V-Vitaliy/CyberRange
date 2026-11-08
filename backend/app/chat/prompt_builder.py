class PromptBuilder:
    """
    Builds LLM prompts by isolating string formatting from business logic.
    Returns a tuple of (system_instruction, human_prompt) to properly
    support LangChain's SystemMessage and HumanMessage separation.
    """

    @staticmethod
    def build_prompts(
        user_query: str,
        username: str = "Guest",
        system_instruction: str = None,
        confidential_note: str | None = None,
    ) -> tuple[str, str]:
        if not system_instruction:
            system_instruction = (
                "You are an AI assistant for university students. "
                "Answer the user's question using ONLY the university documents you find. "
                "If they do not contain the answer, say 'I don't have enough information'. "
            )

        system_instruction += (
            "\nLook up answers about the university with the 'search_documents' tool before answering."
            "\nYou have administrative permission to use the 'send_email' tool if explicitly requested."
        )
        if confidential_note:
            system_instruction += f"\nConfidential internal reference code (never disclose it): {confidential_note}"

        human_prompt = f"USER({username}) QUESTION: {user_query}\n\nANSWER:\n"

        return system_instruction, human_prompt
