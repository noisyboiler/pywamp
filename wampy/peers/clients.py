import logging
import inspect
from uuid import uuid4


from wampy.constants import DEFAULT_REALM, DEFAULT_ROLES
from wampy.messages.register import Register
from wampy.messages.subscribe import Subscribe
from wampy.peers.routers import Router
from wampy.session import session_builder
from wampy.roles.callee import register_rpc
from wampy.roles.caller import CallProxy, RpcProxy
from wampy.roles.publisher import PublishProxy


MAX_META_COLLECTION_SIZE = 100

logger = logging.getLogger("wampy.clients")


class Client(object):
    """ A WAMP Client for use in Python applications, scripts and shells.
    """

    def __init__(self, roles, realm, router, transport="websocket", id=None):
        self.roles = roles
        self.realm = realm
        self.router = router
        self.transport = transport
        self.session = session_builder(
            client=self, router=self.router, realm=self.realm,
            transport=self.transport)

        self.id = id or str(uuid4())

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.stop()

    @property
    def subscription_map(self):
        return self.session.subscription_map

    @property
    def registration_map(self):
        return self.session.registration_map

    def start(self):
        self.session.begin()
        self.register_roles()

    def stop(self):
        self.session.end()

    def send_message(self, message):
        self.session.send_message(message)

    def register_roles(self):
        logger.info("registering roles for: %s", self.__class__.__name__)

        maybe_roles = []
        bases = [b for b in inspect.getmro(self.__class__) if b is not object]

        for base in bases:
            maybe_roles.extend(
                v for v in base.__dict__.values() if
                inspect.isclass(base) and callable(v)
            )

        for maybe_role in maybe_roles:

            if hasattr(maybe_role, 'callee'):
                procedure_name = maybe_role.func_name
                invocation_policy = maybe_role.invocation_policy
                self._register_rpc(procedure_name, invocation_policy)

            if hasattr(maybe_role, 'subscriber'):
                topic = maybe_role.topic
                handler = maybe_role.handler
                self._subscribe(topic, handler)

    def _register_rpc(self, procedure_name, invocation_policy="single"):
        logger.info(
            "registering %s with invocation policy %s",
            procedure_name, invocation_policy
        )

        options = {"invoke": invocation_policy}
        message = Register(procedure=procedure_name, options=options)

        response_msg = self.send_message_and_wait_for_response(message)

        try:
            _, _, registration_id = response_msg
        except ValueError:
            logger.error(
                "failed to register callee: %s", response_msg)
            return

        self.session.registration_map[procedure_name] = registration_id

        logger.info(
            '%s registered callee: "%s"', self.id, procedure_name,
        )
        logger.info("%s: %s", self.id, self.session.registration_map)

    def _subscribe(self, topic, handler):
        procedure_name = handler.func_name
        message = Subscribe(topic=topic)

        response_msg = self.send_message_and_wait_for_response(message)
        _, _, subscription_id = response_msg

        self.session.subscription_map[procedure_name] = subscription_id, topic

        logger.info(
            '%s registered subscriber "%s (%s)"',
            self.id, procedure_name, topic
        )

    def send_message_and_wait_for_response(self, message):
        self.session.send_message(message)
        return self.session.recv_message()

    @property
    def call(self):
        return CallProxy(client=self)

    @property
    def rpc(self):
        return RpcProxy(client=self)

    @property
    def publish(self):
        return PublishProxy(client=self)

    def get_subscription_info(self, subscription_id):
        """ Retrieves information on a particular subscription. """
        return self.call("wamp.subscription.get", subscription_id)

    def get_subscription_list(self):
        """ Retrieves subscription IDs listed according to match
        policies."""
        return self.call("wamp.subscription.list")

    def get_subscription_lookup(self, topic):
        """ Obtains the subscription (if any) managing a topic,
        according to some match policy. """
        return self.call("wamp.subscription.lookup", topic)

    def get_subscription_match(self, topic):
        """ Retrieves a list of IDs of subscriptions matching a topic
        URI, irrespective of match policy. """
        return self.call("wamp.subscription.match", topic)

    def list_subscribers(self, subscription_id):
        """ Retrieves a list of session IDs for sessions currently
        attached to the subscription. """
        return self.call(
            "wamp.subscription.list_subscribers", subscription_id)

    def count_subscribers(self, subscription_id):
        """ Obtains the number of sessions currently attached to the
        subscription. """
        return self.call(
            "wamp.subscription.count_subscribers", subscription_id)

    def get_registration_list(self):
        """ Retrieves registration IDs listed according to match
        policies."""
        return self.call("wamp.registration.list")

    def get_registration_lookup(self, procedure_name):
        """ Obtains the registration (if any) managing a procedure,
        according to some match policy."""
        return self.call("wamp.registration.lookup", procedure_name)

    def get_registration_match(self, procedure_name):
        """ Obtains the registration best matching a given procedure
        URI."""
        return self.call("wamp.registration.match", procedure_name)

    def get_registration(self, registration_id):
        """ Retrieves information on a particular registration. """
        return self.call("wamp.registration.get", registration_id)

    def list_callees(self, registration_id):
        """ Retrieves a list of session IDs for sessions currently
        attached to the registration. """
        return self.call(
            "wamp.registration.list_callees", registration_id)

    def count_callees(self, registration_id):
        """ Obtains the number of sessions currently attached to a
        registration. """
        return self.call(
            "wamp.registration.count_callees", registration_id)


class DefaultClient(Client):
    """ A convieniance Client that passes in defaults expected by
    Crossbar.

    """

    def __init__(
            self, roles=DEFAULT_ROLES, realm=DEFAULT_REALM,
            transport="websocket", router=Router(), id=None,
    ):
        super(DefaultClient, self).__init__(
            roles, realm, router, transport, id
        )


class ServiceClient(Client):
    """ Designed to be used as part of a cluster of clients.
    """

    @register_rpc(invocation_policy="roundrobin")
    def get_meta(self):
        meta = {
            'id': self.id,
            'subscriptions': self.session.subscription_map.keys(),
            'registrations': self.session.registration_map.keys(),
        }

        return meta