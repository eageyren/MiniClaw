from app.memory_service import is_memory_opt_out


def test_explicit_opt_out_phrases_are_recognized():
    assert is_memory_opt_out("这件事别记下来")
    assert is_memory_opt_out("请把这条忘掉")
    assert not is_memory_opt_out("请记住我喜欢听评书")
