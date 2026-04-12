from liquid.auth.classifier import AuthClassifier
from liquid.models.schema import AuthRequirement


class TestAuthClassifier:
    def setup_method(self):
        self.classifier = AuthClassifier()

    def test_tier_a(self):
        auth = AuthRequirement(type="oauth2", tier="A")
        info = self.classifier.classify(auth)
        assert info.tier == "A"
        assert info.action_required == "none"

    def test_tier_b(self):
        auth = AuthRequirement(type="oauth2", tier="B")
        info = self.classifier.classify(auth)
        assert info.tier == "B"
        assert info.action_required == "admin_registration"
        assert "developer" in info.instructions.lower()

    def test_tier_c_api_key(self):
        auth = AuthRequirement(type="api_key", tier="C")
        info = self.classifier.classify(auth)
        assert info.tier == "C"
        assert info.action_required == "provide_api_key"

    def test_tier_c_basic(self):
        auth = AuthRequirement(type="basic", tier="C")
        info = self.classifier.classify(auth)
        assert info.action_required == "provide_credentials"

    def test_tier_c_custom(self):
        auth = AuthRequirement(type="custom", tier="C")
        info = self.classifier.classify(auth)
        assert info.action_required == "manual_configuration"

    def test_docs_url_passed_through(self):
        auth = AuthRequirement(type="bearer", tier="A", docs_url="https://docs.example.com")
        info = self.classifier.classify(auth)
        assert info.docs_url == "https://docs.example.com"
