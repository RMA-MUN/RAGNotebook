"""
序列化模块：
    - LoginSerializer： 登录序列化器，对用户输入的登录信息进行验证
        - validate： 验证用户名或邮箱是否存在，密码是否正确，用户状态是否为激活，最后返回dict

    - UserSerializer： 用户序列化器， 序列化用户信息
        - Meta: 序列化用户模型并返回，使用嵌套序列化器DepartmentSerializer使返回的部门信息更详细

"""

from rest_framework import serializers
from .models import User, UserStatusChoice
from django.db.models import Q

class LoginSerializer(serializers.Serializer):
    """登录序列化器"""
    username = serializers.CharField(required=False, allow_blank=True, help_text="用户名")
    email = serializers.EmailField(required=False, help_text="邮箱")
    password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="密码")

    def validate(self, attrs) -> dict:
        """验证用户名或邮箱是否存在，密码是否正确，用户状态是否为激活"""
        username = attrs.get('username')
        email = attrs.get('email')
        password = attrs.get('password')

        # 验证用户名或邮箱至少提供一个
        if not username and not email:
            raise serializers.ValidationError("用户名或邮箱至少提供一个")

        # 验证用户名或邮箱是否存在
        user_query = User.objects.filter(Q(username=username) | Q(email=email)) if username and email else \
                    User.objects.filter(username=username) if username else \
                    User.objects.filter(email=email)
                     
        if not user_query.exists():
            raise serializers.ValidationError("用户名或邮箱不存在")

        # 获取用户对象
        user = user_query.first()
        
        # 验证密码是否正确
        if not user.check_password(password):
            raise serializers.ValidationError("密码错误")

        # 验证用户状态是否为激活
        if user.status != UserStatusChoice.ACTIVE:
            raise serializers.ValidationError("用户状态异常，请检查是否激活或已被锁定")

        # 将用户对象添加到验证数据中，便于视图使用，减少SQL语句的查询次数
        attrs['user'] = user
        return attrs



class UserSerializer(serializers.ModelSerializer):
    """用户序列化器"""

    class Meta:
        model = User
        fields = ('uuid', 'username', 'email', 'telephone', 'gender', 'bio', 'avatar', 'status', 'date_joined', 'last_login')


class ResetPasswordSerializer(serializers.Serializer):
    """重置密码序列化器"""
    old_password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="旧密码")
    new_password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="新密码")
    confirm_password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="确认密码")

    def validate(self, attrs) -> dict:
        """验证新密码和确认密码是否一致"""
        old_password = attrs.get('old_password')
        new_password = attrs.get('new_password')
        confirm_password = attrs.get('confirm_password')
        # 验证旧密码是否正确
        user = self.context['request'].user
        if not user.check_password(old_password):
            raise serializers.ValidationError("请检查旧密码是否正确")

        # 新密码不能和旧密码相同
        if new_password == old_password:
            raise serializers.ValidationError("新密码不能和旧密码相同")

        if new_password != confirm_password:
            raise serializers.ValidationError("新密码和确认密码不一致")
        
        # 将user添加到验证数据中，以便视图使用
        attrs['user'] = user
        return attrs

class RegisterSerializer(serializers.ModelSerializer):
    """用户注册序列化器"""
    password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="密码", write_only=True)
    confirm_password = serializers.CharField(max_length=20, min_length=6, required=True, help_text="确认密码", write_only=True)
    
    class Meta:
        model = User
        fields = ('username', 'email', 'telephone', 'password', 'confirm_password')
    
    def validate(self, attrs):
        """验证注册信息"""
        email = attrs.get('email')
        telephone = attrs.get('telephone')
        password = attrs.get('password')
        confirm_password = attrs.get('confirm_password')
        
        # 验证邮箱是否已存在
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError({'email': '该邮箱已被注册'})
        
        # 验证电话号码是否已存在
        if telephone and User.objects.filter(telephone=telephone).exists():
            raise serializers.ValidationError({'telephone': '该电话号码已被注册'})
        
        # 验证密码和确认密码是否一致
        if password != confirm_password:
            raise serializers.ValidationError({'confirm_password': '密码和确认密码不一致'})
        
        return attrs
    
    def create(self, validated_data):
        """创建新用户"""
        # 移除确认密码字段
        validated_data.pop('confirm_password')
        # 创建用户并设置密码
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            telephone=validated_data.get('telephone'),
            status=UserStatusChoice.ACTIVE  # 注册后直接激活
        )
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """用户信息更新序列化器"""
    class Meta:
        model = User
        fields = ('username', 'telephone', 'avatar', 'gender', 'bio')  # 添加 avatar、gender 和 bio 字段
    
    def validate(self, attrs):
        """验证更新信息"""
        telephone = attrs.get('telephone')
        user = self.context['request'].user
        
        # 验证电话号码是否已被其他用户使用
        if telephone and User.objects.filter(telephone=telephone).exclude(uuid=user.uuid).exists():
            raise serializers.ValidationError({'telephone': '该电话号码已被注册'})
        
        return attrs
    
    def update(self, instance, validated_data):
        """更新用户信息"""
        return super().update(instance, validated_data)