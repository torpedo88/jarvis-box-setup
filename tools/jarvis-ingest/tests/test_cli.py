from jarvis_ingest.cli import build_parser


def test_parser_requires_source_and_kb():
    parser = build_parser()
    args = parser.parse_args(
        ["--source", "/data/docs", "--knowledge-id", "kb-1",
         "--base-url", "http://x", "--api-key", "sk", "--state", "/tmp/s.json"]
    )
    assert args.source == "/data/docs"
    assert args.knowledge_id == "kb-1"
    assert args.base_url == "http://x"
