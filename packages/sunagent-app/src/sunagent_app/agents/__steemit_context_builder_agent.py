from beem.steem import Steem

class SteemContextBuilder(object):

    def __init__(self, host="", post_key=""):
        self.steem = Steem(host=host, keys=[post_key])

    def _post(self, title, body,author, parent_author="", parent_permlink=""):
        return self.steem.post(title, body, author, parent_author, parent_permlink)

    def _get_discussion(self, author, permlink):
        return self.steem.rpc.