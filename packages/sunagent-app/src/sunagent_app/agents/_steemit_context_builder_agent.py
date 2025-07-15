from beem.steem import Steem
from beemapi.noderpc import NodeRPC
from beem.comment import Comment
from beem.account import Account
from beem.comment import AccountPosts
from beem.discussions import Discussions,Query,Discussions_by_author_before_date,Discussions_by_comments

class SteemContextBuilder(object):

    def __init__(self, node="https://api.steemit.com", post_key=""):
        self.steem = Steem(node=node, keys=[post_key])
        self.post_key = post_key

    def _new_post(self, title, body, tags, account_name):
        res = self.steem.post(title, body, account_name, tags=tags)
        print(res)
        return f"https://steemit.com@{account_name}/{title}"


    def _get_discussions_before(self, account_name, date, limit):
        return Discussions_by_author_before_date(author=account_name, date=date, limit=limit, blockchain_instance=self.steem)


if __name__ == '__main__':
    post_key = "5J9bZcsHtdxRE6kmZdDHnGf92rFXSbRLmU7tdaaua8Ph8EbMNdi"
    stm = SteemContextBuilder(post_key=post_key)
    # print(stm.steem.get_network())
    # print(stm.steem.get_blockchain_name())
    # print(stm.steem.get_api_methods())
    rpc:NodeRPC = stm.steem.rpc
    # print(bobo.name)
    # print(bobo.available_balances)
    # print(bobo.list_all_subscriptions())
    # print(123)
    # dd = Discussions_by_author_before_date("bobotinytiger", blockchain_instance=stm.steem)
    # for ds in dd:
    #     print(ds)
    q= Query(limit=1, start_author="bobotinytiger", start_permlink="test-title")
    for ds in Discussions_by_comments(discussion_query=q, blockchain_instance=stm.steem):
        print( ds)
