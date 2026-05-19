import time
from collections import deque
from typing import Optional, List, Union

from google import genai
from google.genai import types

class GeminiOrchestrator:
    """
    A budget and rate-limit orchestrator wrapper for the Google Gen AI SDK.
    Handles both 'Strict Free Mode' (RPM/token limits) and 'Prepay Budget Mode'.
    """
    
    def __init__(
        self, 
        api_key: str, 
        mode: str = "free", 
        prepay_balance: float = 0.0
    ):
        self.client = genai.Client(api_key=api_key)
        self.mode = mode.lower()
        self.prepay_balance = prepay_balance
        
        # Strictly maintain request timestamps for RPM calculation
        self._request_timestamps = deque()
        
        # Pricing constants per 1,000,000 tokens (Gemini 2.5 Flash defaults)
        self.COST_PER_1M_INPUT = 0.075
        self.COST_PER_1M_OUTPUT = 0.30
        
        self.FREE_TIER_RPM_LIMIT = 12
        self.FREE_TIER_INPUT_LIMIT = 750000
        self.PREPAY_RPM_LIMIT = 300
        
    def _clean_sliding_window(self):
        """Removes request timestamps older than 60 seconds."""
        current_time = time.time()
        while self._request_timestamps and current_time - self._request_timestamps[0] > 60:
            self._request_timestamps.popleft()
            
    def _estimate_cost(self, input_tokens: int, max_output_tokens: int) -> float:
        """Calculates estimated cost for Prepay mode."""
        in_cost = (input_tokens / 1000000) * self.COST_PER_1M_INPUT
        out_cost = (max_output_tokens / 1000000) * self.COST_PER_1M_OUTPUT
        return in_cost + out_cost
        
    def generate_content(
        self, 
        model: str, 
        contents: Union[str, List[str]], 
        config: Optional[types.GenerateContentConfig] = None
    ):
        """
        Wraps client.models.generate_content to enforce limits.
        Proactively blocks or throttles executions based on the active mode.
        """
        self._clean_sliding_window()
        
        # 1. Enforce Sliding Window RPM Limits
        current_rpm = len(self._request_timestamps)
        rpm_cap = self.FREE_TIER_RPM_LIMIT if self.mode == "free" else self.PREPAY_RPM_LIMIT
        
        if current_rpm >= rpm_cap:
            raise Exception(f"RPM Limit Reached: {current_rpm}/{rpm_cap} requests in the last 60 seconds. Call Blocked.")
            
        # 2. Token Counting (Simulated or via SDK)
        try:
            # Using the official SDK count_tokens to accurately evaluate limits
            token_resp = self.client.models.count_tokens(model=model, contents=contents)
            input_tokens = token_resp.total_tokens
        except Exception as e:
            # Fallback estimation if the count_tokens API call fails
            content_text = contents if isinstance(contents, str) else " ".join([str(c) for c in contents])
            input_tokens = len(content_text) // 4
            
        # 3. Mode-Specific Budgeting and Limits
        if self.mode == "free":
            if input_tokens > self.FREE_TIER_INPUT_LIMIT:
                raise Exception(f"Input tokens ({input_tokens}) exceed Free Tier safe limit of {self.FREE_TIER_INPUT_LIMIT}.")
                
        elif self.mode == "prepay":
            max_out = config.max_output_tokens if config and config.max_output_tokens else 8192
            est_cost = self._estimate_cost(input_tokens, max_out)
            
            if self.prepay_balance - est_cost < 0:
                raise Exception(f"Insufficient Prepay Budget. Estimated cost: ${est_cost:.6f}, Balance: ${self.prepay_balance:.6f}.")
        
        # 4. Execute the API Call
        self._request_timestamps.append(time.time())
        response = self.client.models.generate_content(model=model, contents=contents, config=config)
        
        # 5. Post-Execution Accounting
        if self.mode == "prepay":
            try:
                actual_input = response.usage_metadata.prompt_token_count or input_tokens
                actual_output = response.usage_metadata.candidates_token_count or 0
            except AttributeError:
                actual_input = input_tokens
                actual_output = 0
                
            actual_cost = self._estimate_cost(actual_input, actual_output)
            self.prepay_balance -= actual_cost
            
        return response


if __name__ == "__main__":
    print("--- BEACON BUDDY GEMINI ORCHESTRATOR TEST ---")
    # We use a dummy API key for structural simulation testing so we don't accidentally burn real quota.
    # In a real environment, load this securely from os.environ.get("GEMINI_API_KEY")
    DUMMY_KEY = "AIzaSy_dummy_key_for_testing"
    
    print("\n[TEST 1] Strict Free Mode RPM Throttling")
    free_orchestrator = GeminiOrchestrator(api_key=DUMMY_KEY, mode="free")
    
    # We monkey-patch the actual SDK call to simulate a rapid burst without hitting the network
    free_orchestrator.client.models.count_tokens = lambda **kwargs: type('obj', (object,), {'total_tokens': 150})()
    free_orchestrator.client.models.generate_content = lambda **kwargs: type('obj', (object,), {'text': 'Simulated response'})()
    
    success_count = 0
    for i in range(1, 16): # Send 15 requests (Cap is 12)
        try:
            free_orchestrator.generate_content(model="gemini-2.5-flash", contents="Hello World")
            success_count += 1
            print(f"Request {i}: Success")
        except Exception as e:
            print(f"Request {i}: {e}")
            
    print(f"Total Successful Free Requests: {success_count}/12 allowed.")
    
    print("\n[TEST 2] Prepay Budget Mode Drawdown")
    prepay_orchestrator = GeminiOrchestrator(api_key=DUMMY_KEY, mode="prepay", prepay_balance=0.0005)
    
    # Monkey-patch SDK to simulate varying output tokens for dynamic budget deduction
    prepay_orchestrator.client.models.count_tokens = lambda **kwargs: type('obj', (object,), {'total_tokens': 2000})()
    
    class SimulatedResponse:
        def __init__(self, out_tok):
            self.text = "Simulated response"
            self.usage_metadata = type('obj', (object,), {'prompt_token_count': 2000, 'candidates_token_count': out_tok})()
            
    req_num = 1
    while True:
        try:
            print(f"Attempting Request {req_num} | Current Balance: ${prepay_orchestrator.prepay_balance:.6f}")
            # Simulate the response returning exactly 500 output tokens
            prepay_orchestrator.client.models.generate_content = lambda **kwargs: SimulatedResponse(500)
            prepay_orchestrator.generate_content(model="gemini-2.5-flash", contents="Write a story")
            print(f"-> Success! New Balance: ${prepay_orchestrator.prepay_balance:.6f}\n")
            req_num += 1
        except Exception as e:
            print(f"-> Blocked: {e}")
            break