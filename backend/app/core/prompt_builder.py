class PromptBuilder:
    """
    Builds LLM prompts by isolating string formatting from business logic.
    Returns a tuple of (system_instruction, human_prompt) to properly
    support LangChain's SystemMessage and HumanMessage separation.
    """
    @staticmethod
    def build_prompts(user_query: str, context_chunks: list[str], username: str = "Guest", system_instruction: str = None) -> tuple[str, str]:
        context_block = ""
        if context_chunks:
            joined_chunks = "\n".join(context_chunks)
            context_block = (
                f"Context information is below.\n"
                f"---------------------\n"
                f"{joined_chunks}\n"
                f"---------------------\n"
            )

        if not system_instruction:
            system_instruction = (
                "You are an AI assistant for university students. "
                "Answer the user's question using ONLY the provided CONTEXT. "
                "If the answer is not in the context, say 'I don't have enough information'. "
            )

        system_instruction += "\nYou have administrative permission to use the 'send_email' tool if explicitly requested."

        human_prompt = f"CONTEXT:\n{context_block}\n\nUSER({username}) QUESTION: {user_query}\n\nANSWER:\n"

        return system_instruction, human_prompt