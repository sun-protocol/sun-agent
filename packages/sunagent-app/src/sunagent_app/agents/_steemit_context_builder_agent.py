import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from autogen_core import CacheStore
from beem.account import Account
from beem.comment import Comment
from beem.discussions import (
    Discussions_by_author_before_date,
)
from beem.steem import Steem

from sunagent_app._constants import LOGGER_NAME
from sunagent_app.metrics import (
    post_steem_failure_count,
    post_steem_success_count,
    read_steem_failure_count,
    read_steem_success_count,
)

logger = logging.getLogger(LOGGER_NAME)


class SteemContextBuilder(object):
    def __init__(
        self,
        node: str = "https://api.steemit.com",
        post_key: str = "",
        account_name: str = "",
        cache: Optional[CacheStore[str]] = None,
    ):
        self.steem = Steem(node=node, keys=[post_key])
        self.post_key = post_key
        self.account_name = account_name
        self.account = Account(self.account_name, blockchain_instance=self.steem)
        self.cache = cache

    def _new_post(self, title: str, body: str, tags: List[str], self_vote: bool = False) -> str:
        """
        create new post on steemit
        :param title:
        :param body:
        :param tags:
        :return:
        """
        try:
            if tags is None or len(tags) == 0:
                tags = ["web3"]
            permlink = f"{self.account_name}-{time.time()}".replace(".", "")
            res = self.steem.post(
                title=title, body=body, author=self.account_name, tags=tags, self_vote=self_vote, permlink=permlink
            )
            logger.info(f"New post {title} success body: {body} result {res}")
            post_steem_success_count.inc()
            return f"post comment success title : {title}"
        except Exception as e:
            logger.error(f"New post {title} failed e : {str(e)}")
            logger.error(traceback.format_exc())
            post_steem_failure_count.inc()
            return f"New post {title} failed e : {str(e)}"

    def _reply_comment(self, authorperm: str, body: str) -> str:
        """
        reply comment in steemit
        :param authorperm:
        :param body:
        :return: reply info
        """
        try:
            comment = Comment(authorperm=authorperm, blockchain_instance=self.steem)
            result = comment.reply(body, author=self.account_name)
            cache_key = f"comment:{comment.authorperm}"
            if self.cache:
                self.cache.set(cache_key, "processed")
            logger.info(f"Reply comment {authorperm} success body: {body} result {result}")
            read_steem_success_count.inc()
            return f"Reply comment {authorperm} success body: {body}"
        except Exception as e:
            logger.error(f"Reply comment {authorperm} failed e : {str(e)}")
            read_steem_failure_count.inc()
            logger.error(traceback.format_exc())
            return f"Reply comment {authorperm} failed e : {str(e)}"

    def _get_followings(self) -> Any:
        return self.account.get_following()

    def _get_discussions_before(self, account_name: str, date: str, limit: int) -> Any:
        return Discussions_by_author_before_date(
            author=account_name, date=date, limit=limit, blockchain_instance=self.steem
        )

    def _get_followings_new_posts(self, days: int = 1) -> list[Any]:
        """
        获取最近账户关注kol的最新内容
        :param days:
        :return:
        """
        res = []
        for f in self._get_followings():
            try:
                for d in self._get_discussions_before(account_name=f, date="1970-01-01T00:00:00", limit=10):
                    if d.time_elapsed() < timedelta(days=days):
                        dis = {"author": d.author, "body": d.body, "authorperm": d.authorperm}
                        cache_key = f"comment:{d.authorperm}"
                        if self.cache:
                            if self.cache.get(cache_key) is None:
                                res.append(dis)
                                read_steem_success_count.inc()
                        else:
                            res.append(dis)
                            read_steem_success_count.inc()
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"get_followings_new_posts failed e : {str(e)}")
                read_steem_failure_count.inc()
        return res

    def get_new_reply(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        get new replys
        :param days:
        :return:
        """
        mentions = []
        dd = []
        # 时间节点在七天内
        for d in self._get_discussions_before(account_name=self.account_name, date="1970-01-01T00:00:00", limit=100):
            if d.time_elapsed() < timedelta(days=days):
                dd.append(d)
        for d in dd:
            his = self.get_his(d)
            if len(his) > 0:
                for h in his:
                    mentions.append(h)
        return mentions

    def get_his(self, comment: Comment) -> list[Dict[str, Any]]:  # type: ignore[no-any-unimported]
        res = []

        def get_reply(c: Comment, history: list[Dict[str, Any]]) -> None:  # type: ignore[no-any-unimported]
            try:
                cd = c.get_replies()
                if len(cd) > 0:
                    read_steem_success_count.inc(len(cd))
                com = {"author": c.author, "body": c.body, "authorperm": c.authorperm}
                new_history = history.copy()
                if len(cd) == 0 and len(new_history) > 0 and c.author != self.account_name:
                    talk_history = {"history": new_history, "current": com}
                    cache_key = f"comment:{c.authorperm}"
                    if self.cache:
                        if self.cache.get(cache_key) is None:
                            res.append(talk_history)
                    else:
                        res.append(talk_history)
                else:
                    new_history.append(com)
                    for cs in cd:
                        get_reply(cs, new_history)
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"get_his failed e : {str(e)}")
                read_steem_failure_count.inc()

        get_reply(comment, [])
        return res
