"""Document adapters: format parsers and, later, the subprocess that runs them (ADR-032).

The parser libraries are an optional extra (`document`) and are imported only inside the parse
functions, so importing this package never imports them."""
