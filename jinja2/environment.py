# -*- coding: utf-8 -*-
"""
    jinja2.environment
    ~~~~~~~~~~~~~~~~~~

    Provides a class that holds runtime and parsing time options.

    :copyright: 2008 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
"""
import sys
from jinja2.defaults import *
from jinja2.lexer import Lexer
from jinja2.parser import Parser
from jinja2.optimizer import optimize
from jinja2.compiler import generate
from jinja2.runtime import Undefined, Context
from jinja2.exceptions import TemplateSyntaxError
from jinja2.utils import import_string, LRUCache, Markup, missing, concat


# for direct template usage we have up to ten living environments
_spontaneous_environments = LRUCache(10)


def get_spontaneous_environment(*args):
    """Return a new spontaneus environment.  A spontaneus environment is an
    unnamed and unaccessable (in theory) environment that is used for
    template generated from a string and not from the file system.
    """
    try:
        env = _spontaneous_environments.get(args)
    except TypeError:
        return Environment(*args)
    if env is not None:
        return env
    _spontaneous_environments[args] = env = Environment(*args)
    env.shared = True
    return env


def create_cache(size):
    """Return the cache class for the given size."""
    if size == 0:
        return None
    if size < 0:
        return {}
    return LRUCache(size)


def load_extensions(environment, extensions):
    """Load the extensions from the list and bind it to the environment.
    Returns a dict of instanciated environments.
    """
    result = {}
    for extension in extensions:
        if isinstance(extension, basestring):
            extension = import_string(extension)
        result[extension.identifier] = extension(environment)
    return result


def _environment_sanity_check(environment):
    """Perform a sanity check on the environment."""
    assert issubclass(environment.undefined, Undefined), 'undefined must ' \
           'be a subclass of undefined because filters depend on it.'
    assert environment.block_start_string != \
           environment.variable_start_string != \
           environment.comment_start_string, 'block, variable and comment ' \
           'start strings must be different'
    return environment


class Environment(object):
    """The core component of Jinja is the `Environment`.  It contains
    important shared variables like configuration, filters, tests,
    globals and others.  Instances of this class may be modified if
    they are not shared and if no template was loaded so far.
    Modifications on environments after the first template was loaded
    will lead to surprising effects and undefined behavior.

    Here the possible initialization parameters:

        `block_start_string`
            The string marking the begin of a block.  Defaults to ``'{%'``.

        `block_end_string`
            The string marking the end of a block.  Defaults to ``'%}'``.

        `variable_start_string`
            The string marking the begin of a print statement.
            Defaults to ``'{{'``.

        `variable_stop_string`
            The string marking the end of a print statement.  Defaults to
            ``'}}'``.

        `comment_start_string`
            The string marking the begin of a comment.  Defaults to ``'{#'``.

        `comment_end_string`
            The string marking the end of a comment.  Defaults to ``'#}'``.

        `line_statement_prefix`
            If given and a string, this will be used as prefix for line based
            statements.  See also :ref:`line-statements`.

        `trim_blocks`
            If this is set to ``True`` the first newline after a block is
            removed (block, not variable tag!).  Defaults to `False`.

        `extensions`
            List of Jinja extensions to use.  This can either be import paths
            as strings or extension classes.  For more information have a
            look at :ref:`the extensions documentation <jinja-extensions>`.

        `optimized`
            should the optimizer be enabled?  Default is `True`.

        `undefined`
            :class:`Undefined` or a subclass of it that is used to represent
            undefined values in the template.

        `finalize`
            A callable that finalizes the variable.  Per default no finalizing
            is applied.

        `autoescape`
            If set to true the XML/HTML autoescaping feature is enabled.

        `loader`
            The template loader for this environment.

        `cache_size`
            The size of the cache.  Per default this is ``50`` which means
            that if more than 50 templates are loaded the loader will clean
            out the least recently used template.  If the cache size is set to
            ``0`` templates are recompiled all the time, if the cache size is
            ``-1`` the cache will not be cleaned.

        `auto_reload`
            Some loaders load templates from locations where the template
            sources may change (ie: file system or database).  If
            `auto_reload` is set to `True` (default) every time a template is
            requested the loader checks if the source changed and if yes, it
            will reload the template.  For higher performance it's possible to
            disable that.
    """

    #: if this environment is sandboxed.  Modifying this variable won't make
    #: the environment sandboxed though.  For a real sandboxed environment
    #: have a look at jinja2.sandbox
    sandboxed = False

    #: True if the environment is just an overlay
    overlay = False

    #: the environment this environment is linked to if it is an overlay
    linked_to = None

    #: shared environments have this set to `True`.  A shared environment
    #: must not be modified
    shared = False

    def __init__(self,
                 block_start_string=BLOCK_START_STRING,
                 block_end_string=BLOCK_END_STRING,
                 variable_start_string=VARIABLE_START_STRING,
                 variable_end_string=VARIABLE_END_STRING,
                 comment_start_string=COMMENT_START_STRING,
                 comment_end_string=COMMENT_END_STRING,
                 line_statement_prefix=LINE_STATEMENT_PREFIX,
                 trim_blocks=False,
                 extensions=(),
                 optimized=True,
                 undefined=Undefined,
                 finalize=None,
                 autoescape=False,
                 loader=None,
                 cache_size=50,
                 auto_reload=True):
        # !!Important notice!!
        #   The constructor accepts quite a few arguments that should be
        #   passed by keyword rather than position.  However it's important to
        #   not change the order of arguments because it's used at least
        #   internally in those cases:
        #       -   spontaneus environments (i18n extension and Template)
        #       -   unittests
        #   If parameter changes are required only add parameters at the end
        #   and don't change the arguments (or the defaults!) of the arguments
        #   existing already.

        # lexer / parser information
        self.block_start_string = block_start_string
        self.block_end_string = block_end_string
        self.variable_start_string = variable_start_string
        self.variable_end_string = variable_end_string
        self.comment_start_string = comment_start_string
        self.comment_end_string = comment_end_string
        self.line_statement_prefix = line_statement_prefix
        self.trim_blocks = trim_blocks

        # runtime information
        self.undefined = undefined
        self.optimized = optimized
        self.finalize = finalize
        self.autoescape = autoescape

        # defaults
        self.filters = DEFAULT_FILTERS.copy()
        self.tests = DEFAULT_TESTS.copy()
        self.globals = DEFAULT_NAMESPACE.copy()

        # set the loader provided
        self.loader = loader
        self.cache = create_cache(cache_size)
        self.auto_reload = auto_reload

        # load extensions
        self.extensions = load_extensions(self, extensions)

        _environment_sanity_check(self)

    def extend(self, **attributes):
        """Add the items to the instance of the environment if they do not exist
        yet.  This is used by :ref:`extensions <writing-extensions>` to register
        callbacks and configuration values without breaking inheritance.
        """
        for key, value in attributes.iteritems():
            if not hasattr(self, key):
                setattr(self, key, value)

    def overlay(self, block_start_string=missing, block_end_string=missing,
                variable_start_string=missing, variable_end_string=missing,
                comment_start_string=missing, comment_end_string=missing,
                line_statement_prefix=missing, trim_blocks=missing,
                extensions=missing, optimized=missing, undefined=missing,
                finalize=missing, autoescape=missing, loader=missing,
                cache_size=missing, auto_reload=missing):
        """Create a new overlay environment that shares all the data with the
        current environment except of cache and the overriden attributes.
        Extensions cannot be removed for a overlayed environment.  A overlayed
        environment automatically gets all the extensions of the environment it
        is linked to plus optional extra extensions.

        Creating overlays should happen after the initial environment was set
        up completely.  Not all attributes are truly linked, some are just
        copied over so modifications on the original environment may not shine
        through.
        """
        args = dict(locals())
        del args['self'], args['cache_size'], args['extensions']

        rv = object.__new__(self.__class__)
        rv.__dict__.update(self.__dict__)
        rv.overlay = True
        rv.linked_to = self

        for key, value in args.iteritems():
            if value is not missing:
                setattr(rv, key, value)

        if cache_size is not missing:
            rv.cache = create_cache(cache_size)

        rv.extensions = {}
        for key, value in self.extensions.iteritems():
            rv.extensions[key] = value.bind(rv)
        if extensions is not missing:
            rv.extensions.update(load_extensions(extensions))

        return _environment_sanity_check(rv)

    @property
    def lexer(self):
        """Return a fresh lexer for the environment."""
        return Lexer(self)

    def subscribe(self, obj, argument):
        """Get an item or attribute of an object."""
        try:
            return getattr(obj, str(argument))
        except (AttributeError, UnicodeError):
            try:
                return obj[argument]
            except (TypeError, LookupError):
                return self.undefined(obj=obj, name=argument)

    def parse(self, source, filename=None):
        """Parse the sourcecode and return the abstract syntax tree.  This
        tree of nodes is used by the compiler to convert the template into
        executable source- or bytecode.  This is useful for debugging or to
        extract information from templates.

        If you are :ref:`developing Jinja2 extensions <writing-extensions>`
        this gives you a good overview of the node tree generated.
        """
        try:
            return Parser(self, source, filename).parse()
        except TemplateSyntaxError, e:
            from jinja2.debug import translate_syntax_error
            exc_type, exc_value, tb = translate_syntax_error(e)
            raise exc_type, exc_value, tb

    def lex(self, source, filename=None):
        """Lex the given sourcecode and return a generator that yields
        tokens as tuples in the form ``(lineno, token_type, value)``.
        This can be useful for :ref:`extension development <writing-extensions>`
        and debugging templates.
        """
        return self.lexer.tokeniter(source, filename)

    def compile(self, source, name=None, filename=None, raw=False):
        """Compile a node or template source code.  The `name` parameter is
        the load name of the template after it was joined using
        :meth:`join_path` if necessary, not the filename on the file system.
        the `filename` parameter is the estimated filename of the template on
        the file system.  If the template came from a database or memory this
        can be omitted.

        The return value of this method is a python code object.  If the `raw`
        parameter is `True` the return value will be a string with python
        code equivalent to the bytecode returned otherwise.  This method is
        mainly used internally.
        """
        if isinstance(source, basestring):
            source = self.parse(source, filename)
        if self.optimized:
            node = optimize(source, self)
        source = generate(node, self, name, filename)
        if raw:
            return source
        if filename is None:
            filename = '<template>'
        elif isinstance(filename, unicode):
            filename = filename.encode('utf-8')
        return compile(source, filename, 'exec')

    def join_path(self, template, parent):
        """Join a template with the parent.  By default all the lookups are
        relative to the loader root so this method returns the `template`
        parameter unchanged, but if the paths should be relative to the
        parent template, this function can be used to calculate the real
        template name.

        Subclasses may override this method and implement template path
        joining here.
        """
        return template

    def get_template(self, name, parent=None, globals=None):
        """Load a template from the loader.  If a loader is configured this
        method ask the loader for the template and returns a :class:`Template`.
        If the `parent` parameter is not `None`, :meth:`join_path` is called
        to get the real template name before loading.

        The `globals` parameter can be used to provide temlate wide globals.
        These variables are available in the context at render time.

        If the template does not exist a :exc:`TemplateNotFound` exception is
        raised.
        """
        if self.loader is None:
            raise TypeError('no loader for this environment specified')
        if parent is not None:
            name = self.join_path(name, parent)

        if self.cache is not None:
            template = self.cache.get(name)
            if template is not None and (not self.auto_reload or \
                                         template.is_up_to_date):
                return template

        template = self.loader.load(self, name, self.make_globals(globals))
        if self.cache is not None:
            self.cache[name] = template
        return template

    def from_string(self, source, globals=None, template_class=None):
        """Load a template from a string.  This parses the source given and
        returns a :class:`Template` object.
        """
        globals = self.make_globals(globals)
        cls = template_class or self.template_class
        return cls.from_code(self, self.compile(source), globals, None)

    def make_globals(self, d):
        """Return a dict for the globals."""
        if d is None:
            return self.globals
        return dict(self.globals, **d)


class Template(object):
    """The central template object.  This class represents a compiled template
    and is used to evaluate it.

    Normally the template object is generated from an :class:`Environment` but
    it also has a constructor that makes it possible to create a template
    instance directly using the constructor.  It takes the same arguments as
    the environment constructor but it's not possible to specify a loader.

    Every template object has a few methods and members that are guaranteed
    to exist.  However it's important that a template object should be
    considered immutable.  Modifications on the object are not supported.

    Template objects created from the constructor rather than an environment
    do have an `environment` attribute that points to a temporary environment
    that is probably shared with other templates created with the constructor
    and compatible settings.

    >>> template = Template('Hello {{ name }}!')
    >>> template.render(name='John Doe')
    u'Hello John Doe!'

    >>> stream = template.stream(name='John Doe')
    >>> stream.next()
    u'Hello John Doe!'
    >>> stream.next()
    Traceback (most recent call last):
        ...
    StopIteration
    """

    def __new__(cls, source,
                block_start_string='{%',
                block_end_string='%}',
                variable_start_string='{{',
                variable_end_string='}}',
                comment_start_string='{#',
                comment_end_string='#}',
                line_statement_prefix=None,
                trim_blocks=False,
                extensions=(),
                optimized=True,
                undefined=Undefined,
                finalize=None,
                autoescape=False):
        env = get_spontaneous_environment(
            block_start_string, block_end_string, variable_start_string,
            variable_end_string, comment_start_string, comment_end_string,
            line_statement_prefix, trim_blocks, tuple(extensions), optimized,
            undefined, finalize, autoescape, None, 0, False)
        return env.from_string(source, template_class=cls)

    @classmethod
    def from_code(cls, environment, code, globals, uptodate=None):
        """Creates a template object from compiled code and the globals.  This
        is used by the loaders and environment to create a template object.
        """
        t = object.__new__(cls)
        namespace = {
            'environment':          environment,
            '__jinja_template__':   t
        }
        exec code in namespace
        t.environment = environment
        t.name = namespace['name']
        t.filename = code.co_filename
        t.root_render_func = namespace['root']
        t.blocks = namespace['blocks']
        t.globals = globals

        # debug and loader helpers
        t._debug_info = namespace['debug_info']
        t._uptodate = uptodate

        return t

    def render(self, *args, **kwargs):
        """This method accepts the same arguments as the `dict` constructor:
        A dict, a dict subclass or some keyword arguments.  If no arguments
        are given the context will be empty.  These two calls do the same::

            template.render(knights='that say nih')
            template.render({'knights': 'that say nih'})

        This will return the rendered template as unicode string.
        """
        try:
            return concat(self._generate(*args, **kwargs))
        except:
            from jinja2.debug import translate_exception
            exc_type, exc_value, tb = translate_exception(sys.exc_info())
            raise exc_type, exc_value, tb

    def stream(self, *args, **kwargs):
        """Works exactly like :meth:`generate` but returns a
        :class:`TemplateStream`.
        """
        return TemplateStream(self.generate(*args, **kwargs))

    def generate(self, *args, **kwargs):
        """For very large templates it can be useful to not render the whole
        template at once but evaluate each statement after another and yield
        piece for piece.  This method basically does exactly that and returns
        a generator that yields one item after another as unicode strings.

        It accepts the same arguments as :meth:`render`.
        """
        try:
            for item in self._generate(*args, **kwargs):
                yield item
        except:
            from jinja2.debug import translate_exception
            exc_type, exc_value, tb = translate_exception(sys.exc_info())
            raise exc_type, exc_value, tb

    def _generate(self, *args, **kwargs):
        """Creates a new context and generates the template."""
        return self.root_render_func(self.new_context(dict(*args, **kwargs)))

    def new_context(self, vars=None, shared=False):
        """Create a new template context for this template.  The vars
        provided will be passed to the template.  Per default the globals
        are added to the context, if shared is set to `True` the data
        provided is used as parent namespace.  This is used to share the
        same globals in multiple contexts without consuming more memory.
        (This works because the context does not modify the parent dict)
        """
        if vars is None:
            vars = {}
        if shared:
            parent = vars
        else:
            parent = dict(self.globals, **vars)
        return Context(self.environment, parent, self.name, self.blocks)

    def make_module(self, vars=None, shared=False):
        """This method works like the :attr:`module` attribute when called
        without arguments but it will evaluate the template every call
        rather then caching the template.  It's also possible to provide
        a dict which is then used as context.  The arguments are the same
        as fo the :meth:`new_context` method.
        """
        return TemplateModule(self, self.new_context(vars, shared))

    @property
    def module(self):
        """The template as module.  This is used for imports in the
        template runtime but is also useful if one wants to access
        exported template variables from the Python layer:

        >>> t = Template('{% macro foo() %}42{% endmacro %}23')
        >>> unicode(t.module)
        u'23'
        >>> t.module.foo()
        u'42'
        """
        if hasattr(self, '_module'):
            return self._module
        self._module = rv = self.make_module()
        return rv

    def get_corresponding_lineno(self, lineno):
        """Return the source line number of a line number in the
        generated bytecode as they are not in sync.
        """
        for template_line, code_line in reversed(self.debug_info):
            if code_line <= lineno:
                return template_line
        return 1

    @property
    def is_up_to_date(self):
        """If this variable is `False` there is a newer version available."""
        if self._uptodate is None:
            return True
        return self._uptodate()

    @property
    def debug_info(self):
        """The debug info mapping."""
        return [tuple(map(int, x.split('='))) for x in
                self._debug_info.split('&')]

    def __repr__(self):
        if self.name is None:
            name = 'memory:%x' % id(self)
        else:
            name = repr(self.name)
        return '<%s %s>' % (self.__class__.__name__, name)


class TemplateModule(object):
    """Represents an imported template.  All the exported names of the
    template are available as attributes on this object.  Additionally
    converting it into an unicode- or bytestrings renders the contents.
    """

    def __init__(self, template, context):
        # don't alter this attribute unless you change it in the
        # compiler too.  The Include without context passing directly
        # uses the mangled name.  The reason why we use a mangled one
        # is to avoid name clashes with macros with those names.
        self.__body_stream = list(template.root_render_func(context))
        self.__dict__.update(context.get_exported())
        self.__name__ = template.name

    __html__ = lambda x: Markup(concat(x.__body_stream))
    __unicode__ = lambda x: unicode(concat(x.__body_stream))

    def __str__(self):
        return unicode(self).encode('utf-8')

    def __repr__(self):
        if self.__name__ is None:
            name = 'memory:%x' % id(self)
        else:
            name = repr(self.name)
        return '<%s %s>' % (self.__class__.__name__, name)


class TemplateStream(object):
    """A template stream works pretty much like an ordinary python generator
    but it can buffer multiple items to reduce the number of total iterations.
    Per default the output is unbuffered which means that for every unbuffered
    instruction in the template one unicode string is yielded.

    If buffering is enabled with a buffer size of 5, five items are combined
    into a new unicode string.  This is mainly useful if you are streaming
    big templates to a client via WSGI which flushes after each iteration.
    """

    def __init__(self, gen):
        self._gen = gen
        self._next = gen.next
        self.buffered = False

    def disable_buffering(self):
        """Disable the output buffering."""
        self._next = self._gen.next
        self.buffered = False

    def enable_buffering(self, size=5):
        """Enable buffering.  Buffer `size` items before yielding them."""
        if size <= 1:
            raise ValueError('buffer size too small')

        def generator():
            buf = []
            c_size = 0
            push = buf.append
            next = self._gen.next

            while 1:
                try:
                    while c_size < size:
                        c = next()
                        push(c)
                        if c:
                            c_size += 1
                except StopIteration:
                    if not c_size:
                        return
                yield concat(buf)
                del buf[:]
                c_size = 0

        self.buffered = True
        self._next = generator().next

    def __iter__(self):
        return self

    def next(self):
        return self._next()


# hook in default template class.  if anyone reads this comment: ignore that
# it's possible to use custom templates ;-)
Environment.template_class = Template
