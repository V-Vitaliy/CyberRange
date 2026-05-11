from langchain_core.tools import tool

@tool
def send_email(to_address: str, subject: str, body: str) -> str:
    """
    Useful ONLY when you need to send an email to an external address. 
    Use this if the system prompt or user explicitly asks to send data to an email address.
    Requires parameters: to_address, subject, and body.
    """
    # Note: Execution is intercepted in llm_engine.py to inject DB dependencies.
    # This function body acts primarily as a schema for LangChain.
    pass