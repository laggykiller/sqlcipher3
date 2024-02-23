from conan import ConanFile

class OpensslRecipe(ConanFile):
    def requirements(self):
        self.requires("openssl/1.1.1w")