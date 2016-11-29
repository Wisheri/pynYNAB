import json
import logging
from datetime import datetime
from uuid import UUID

from aenum import Enum
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import event
from sqlalchemy import orm
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.declarative import declared_attr

from pynYNAB import KeyGenerator
from pynYNAB.schema.types import NYNAB_GUID, AmountType

logger = logging.getLogger('pynYNAB')
from sqlalchemy import inspect


def undef():
    pass


class AccountTypes(Enum):
    undef = 'undef'
    Checking = 'Checking'
    Savings = 'Savings'
    CreditCard = 'CreditCard'
    Cash = 'Cash'
    LineOfCredit = 'LineOfCredit'
    Paypal = 'Paypal'
    MerchantAccount = 'MerchantAccount'
    InvestmentAccount = 'InvestmentAccount'
    Mortgage = 'Mortgage'
    OtherAsset = 'OtherAsset'
    OtherLiability = 'OtherLiability'


on_budget_dict = dict(
    undef=None,
    Checking=True,
    Savings=True,
    CreditCard=True,
    Cash=True,
    LineOfCredit=True,
    Paypal=True,
    MerchantAccount=True,
    InvestmentAccount=False,
    Mortgage=False,
    OtherAsset=False,
    OtherLiability=False,
)
on_budget_dict[None] = None


class ComplexEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Entity):
            return obj.getdict(treat=True)
        elif isinstance(obj, UUID):
            return str(obj)
        elif obj == undef:
            return
        else:
            return json.JSONEncoder.default(self, obj)


class UnknowEntityFieldValueError(Exception):
    pass


ignored_fields_for_hash = ['id', 'credit_amount', 'cash_amount', 'feature_flags']


# adapted from http://stackoverflow.com/a/2954373/1685379
def addprop(inst, name, method, setter=None, cleaner=None):
    cls = type(inst)
    if not hasattr(cls, '__perinstance'):
        cls = type(cls.__name__, (cls,), {})
        cls.__perinstance = True
        inst.__class__ = cls
    p = property(method)
    setattr(cls, name, p)
    if setter is not None:
        setattr(cls, name, p.setter(setter))
    if cleaner is not None:
        setattr(cls, 'clean_' + name, cleaner)
    return p


class BaseModel(object):
    id = Column(NYNAB_GUID, primary_key=True, default=KeyGenerator.generateuuid)
    is_tombstone = Column(Boolean, default=False)

    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()

    @property
    def listfields(self):
        relations = inspect(self.__class__).relationships
        return {k: relations[k].mapper.class_ for k in relations.keys()}

    @property
    def scalarfields(self):
        scalarcolumns = self.__table__.columns
        return {k: scalarcolumns[k].type.__class__.__name__ for k in scalarcolumns.keys()}

    @property
    def allfields(self):
        z = self.scalarfields.copy()
        z.update(self.listfields)
        return z


def configure_listener(mapper, class_):
    """Establish attribute setters for every default-holding column on the
    given mapper."""

    # iterate through ColumnProperty objects
    for col_attr in mapper.column_attrs:

        # look at the Column mapped by the ColumnProperty
        # (we look at the first column in the less common case
        # of a property mapped to multiple columns at once)
        column = col_attr.columns[0]

        # if the Column has a "default", set up a listener
        if column.default is not None:
            default_listener(col_attr, column.default)

    for rel_attr in mapper.relationships:
        expectedtype_listener(rel_attr)


def expectedtype_listener(rel_attr):
    @event.listens_for(rel_attr, 'append')
    def append(target, value, initiator):
        expected_type = initiator.parent_token.mapper.class_
        value_type = type(value)
        if expected_type != value_type:
            raise ValueError('expect a %s, received a %s ' % (expected_type, value_type))


def default_listener(col_attr, default):
    """Establish a default-setting listener.

    Given a class_, attrname, and a :class:`.DefaultGenerator` instance.
    The default generator should be a :class:`.ColumnDefault` object with a
    plain Python value or callable default; otherwise, the appropriate behavior
    for SQL functions and defaults should be determined here by the
    user integrating this feature.

    """
    @event.listens_for(col_attr, "init_scalar", retval=True, propagate=True)
    def init_scalar(target, value, dict_):

        if default.is_callable:
            # the callable of ColumnDefault always accepts a context
            # argument; we can pass it as None here.
            value = default.arg(None)
        elif default.is_scalar:
            value = default.arg
        else:
            # default is a Sequence, a SQL expression, server
            # side default generator, or other non-Python-evaluable
            # object.  The feature here can't easily support this.   This
            # can be made to return None, rather than raising,
            # or can procure a connection from an Engine
            # or Session and actually run the SQL, if desired.
            raise NotImplementedError(
                "Can't invoke pre-default for a SQL-level column default")

        # set the value in the given dict_; this won't emit any further
        # attribute set events or create attribute "history", but the value
        # will be used in the INSERT statement
        dict_[col_attr.key] = value

        # return the value as well
        return value


class Entity(BaseModel):
    def getdict(self, treat=False):
        entityDict = {key: getattr(self, key) for key in self.scalarfields}
        if treat:
            for column in self.__table__.columns:
                if column.name in entityDict and entityDict[column.name] is not None:
                    if column.type.__class__.__name__ == Date.__name__:
                        entityDict[column.name] = entityDict[column.name].strftime('%Y-%m-%d')
                    if column.type.__class__.__name__ == NYNAB_GUID.__name__:
                        entityDict[column.name] = str(entityDict[column.name])
                    if column.type.__class__.__name__ == AmountType.__name__:
                        entityDict[column.name] *= 100
        return entityDict

    def __unicode__(self):
        return self.__str__()

    def __str__(self):
        return self.getdict().__str__()

    def __repr__(self):
        return self.getdict().__str__()

    def __eq__(self, other):
        try:
            return self.__key() == other.__key()
        except:
            return False

    def __key(self):
        return tuple(self.getdict().items())

    def __hash__(self):
        return hash(self.__key())

    def copy(self):
        return type(self)(**self.getdict())

    @classmethod
    def from_dict(cls, entityDict, treat=False):
        if treat:
            for column in cls.__table__.columns:
                if column.name in entityDict and entityDict[column.name] is not None:
                    if column.type.__class__.__name__ == Date.__name__:
                        entityDict[column.name] = datetime.strptime(entityDict[column.name], '%Y-%m-%d').date()
                    if column.type.__class__.__name__ == NYNAB_GUID.__name__:
                        entityDict[column.name] = entityDict[column.name].split('/')[-1]
                    if column.type.__class__.__name__ == AmountType.__name__:
                        entityDict[column.name] /= 100

        return cls(**entityDict)

Base = declarative_base(cls=Entity)

event.listen(BaseModel, 'mapper_configured', configure_listener, propagate=True)


class DictDiffer(object):
    """
    Calculate the difference between two dictionaries as:
    (1) items added
    (2) items removed
    (3) keys same in both but changed values
    (4) keys same in both and unchanged values
    """

    def __init__(self, current_dict, past_dict):
        self.current_dict, self.past_dict = current_dict, past_dict
        self.set_current, self.set_past = set(current_dict.keys()), set(past_dict.keys())
        self.intersect = self.set_current.intersection(self.set_past)

    def added(self):
        return self.set_current - self.intersect

    def removed(self):
        return self.set_past - self.intersect

    def changed(self):
        return set(o for o in self.intersect if self.past_dict[o] != self.current_dict[o])

    def unchanged(self):
        return set(o for o in self.intersect if self.past_dict[o] == self.current_dict[o])


class RootEntity(BaseModel):
    previous_map = {}

    def get_changed_dict(self, treat=False):
        changed_entities = self.get_changed_entities()
        changed_dict={}
        for key in changed_entities:
            changed_dict[key]=map(lambda entity:entity.getdict(treat),changed_entities[key])

    def get_changed_entities(self):
        current_map = self.getmap()
        diff_map = {}

        for key in current_map:
            if key not in diff_map:
                diff_map[key] = {}
            if isinstance(current_map[key], dict):
                if key in self.previous_map:
                    diff = DictDiffer(current_map[key], self.previous_map[key])
                    for obj_id in diff.added() | diff.changed():
                        obj = current_map[key][obj_id]
                        objc = obj.copy()
                        diff_map[key][obj_id] = objc
                    for obj_id in diff.removed():
                        obj = self.previous_map[key][obj_id]
                        objc = obj.copy()
                        objc.is_tombstone = True
                        diff_map[key][obj.id] = objc

                else:
                    diff_map[key] = current_map[key]
        returnvalue = {}
        for key, value in diff_map.items():
            if isinstance(value, dict):
                if value:
                    returnvalue[key] = list(value.values())

        return returnvalue

    def update_from_changed_entities(self, changed_entities):
        if changed_entities is None:
            return
        current_map = self.getmap()
        for key in self.listfields:
            listattr = getattr(self, key)
            if key in changed_entities:
                for entity in changed_entities[key]:
                    if hasattr(entity, 'is_tombstone') and entity.is_tombstone:
                        if entity.id in current_map[key]:
                            listattr.remove(current_map[key][entity.id])
                        else:
                            pass
                    try:
                        current_map[key][entity.id].__dict__.update(entity.getdict())
                    except KeyError:
                        listattr.append(entity)

    def __init__(self):
        super(RootEntity, self).__init__()
        self.clear_changed_entities()

    @orm.reconstructor
    def clear_changed_entities(self):
        self.previous_map = self.getmap()

    def getmap(self) -> dict:
        objs_dict = {}
        for key in self.listfields:
            objs_dict[key] = {}
            if getattr(self, key) is not None:
                for instance in getattr(self, key):
                    objs_dict[key][str(instance.id)] = instance
        for key in self.scalarfields:
            objs_dict[key] = getattr(self, key)
        return objs_dict
