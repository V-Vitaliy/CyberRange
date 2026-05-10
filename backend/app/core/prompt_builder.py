class PromptBuilder:
    """
    Builds LLM prompts by concatenating instructions, context chunks, and user queries.
    Isolates string formatting from business logic.
    """
    @staticmethod
    def build_prompt(user_query: str, context_chunks: list[str], username: str = "Guest", system_instruction: str = None) -> str:
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
                "If the answer is not in the context, say 'I don't have enough information'."
            )

        return f"""{system_instruction}

                CONTEXT: 
                {context_block}
                
                USER({username}) QUESTION: {user_query}
                
                ANSWER:
                """