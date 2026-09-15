from data_sources.tracking.enums import ChannelClass
from data_sources.tracking.transforms.ai_referrals import classify_channel
from data_sources.tracking.transforms.brand_label import BrandClassifier


def test_brand_classifier_matches_approved_variants():
    classifier = BrandClassifier(terms=["sunnystep", "sunny step", "gosunnystep"])
    assert classifier.is_brand("Sunnystep shoes")
    assert classifier.is_brand("sunny-step walking shoes")
    assert classifier.is_brand("go sunnystep store")
    assert classifier.version == "brand_rules_v1"


def test_brand_classifier_rejects_near_and_competitor_terms():
    classifier = BrandClassifier(terms=["sunnystep", "sunny step"])
    assert not classifier.is_brand("sunny weather shoes singapore")
    assert not classifier.is_brand("anothersole comfortable shoes")
    assert not classifier.is_brand("lucca vudor")
    assert not classifier.is_brand("step sunny")


def test_channel_classifier_ai_first_then_organic():
    ai = [
        "chatgpt.com",
        "chat.openai.com",
        "perplexity.ai",
        "gemini.google.com",
        "copilot.microsoft.com",
        "bing.com/chat",
    ]
    assert classify_channel("https://ChatGPT.com", "referral", ai) == ChannelClass.AI_REFERRAL
    assert classify_channel("chat.openai.com", "referral", ai) == ChannelClass.AI_REFERRAL
    assert classify_channel("www.perplexity.ai", "referral", ai) == ChannelClass.AI_REFERRAL
    assert classify_channel("gemini.google.com", "organic", ai) == ChannelClass.AI_REFERRAL
    assert classify_channel("google", "organic", ai) == ChannelClass.ORGANIC_SEARCH
    assert classify_channel("bing", "organic", ai) == ChannelClass.ORGANIC_SEARCH
    assert classify_channel("facebook", "cpc", ai) == ChannelClass.OTHER
    assert classify_channel("unknown-ai.example", "referral", ai) == ChannelClass.OTHER
