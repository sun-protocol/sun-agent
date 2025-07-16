from prometheus_client import Counter, Gauge, Histogram, start_http_server

# 发推监控
post_tweet_success_count = Counter("post_tweet_success_count", "Number of successful post tweets")
post_tweet_failure_count = Counter("post_tweet_failure_count", "Number of failed post tweets")

# 读推监控
read_tweet_success_count = Counter("read_tweet_success_count", "Number of successful read tweets")
read_tweet_failure_count = Counter("read_tweet_failure_count", "Number of failed read tweets")

# 失败监控
model_api_failure_count = Counter("model_api_failure_count", "Number of Model API failures")
model_api_success_count = Counter("model_api_success_count", "Number of Model API success")

post_twitter_quota_limit = Gauge("post_twitter_quota_limit", "Twitter quota limit")
get_twitter_quota_limit = Gauge("get_twitter_quota_limit", "Twitter quota limit")

# Twitter 账号封禁监控
twitter_account_banned = Gauge("twitter_account_banned", "Twitter account banned status (1 = banned, 0 = active)")

twitter_api_failure_count = Counter("twitter_api_failure_count", "Number of Twitter API failures")
# 链路耗时监控
link_duration = Histogram("link_duration_seconds", "Link duration")

# LLM Token 使用监控
llm_token_usage = Counter("llm_token_usage", "LLM token usage")

api_requests_total = Counter("api_requests_total", "Total API requests", ["endpoint", "status"])
api_request_duration = Histogram("api_request_duration_seconds", "API request duration", ["endpoint"])

start_server = start_http_server
