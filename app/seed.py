from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import CardBalance, CardType, Rarity, Role, User

CARDS = [
    ("QINGLONG", "青龙", Rarity.NORMAL, 2475, "此刻最值得你主动争取的机会是什么？", "青龙象征生发与行动。把注意力放在可迈出的第一步，而不是等待万事俱备。"),
    ("ZHUQUE", "朱雀", Rarity.NORMAL, 2475, "哪一句真心话正等待你表达？", "朱雀象征表达与热情。清晰而温和地说出真实需要，会让关系重新流动。"),
    ("BAIHU", "白虎", Rarity.NORMAL, 2475, "你需要为哪件事划清边界？", "白虎象征决断与守护。适时拒绝并非冷漠，而是在保护真正重要的事。"),
    ("XUANWU", "玄武", Rarity.NORMAL, 2475, "什么能让你在变化中保持稳定？", "玄武象征沉静与积累。回到规律、耐心和长期主义，答案会逐渐清晰。"),
    ("QILIN", "麒麟", Rarity.RARE, 100, "如果好运已经到来，你准备如何接住它？", "麒麟象征祥瑞与整合。珍惜当下的善意，也把所得转化为照亮他人的力量。"),
]

USERS = [
    ("小满（普通用户）", Role.USER),
    ("青禾（普通大师）", Role.MASTER),
    ("知远（传承大师）", Role.INHERITOR),
    ("阿星（普通用户）", Role.USER),
]


def seed() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as session:
        if not session.scalar(select(CardType.id).limit(1)):
            session.add_all(
                [
                    CardType(
                        code=code,
                        name=name,
                        rarity=rarity,
                        draw_weight=weight,
                        question=question,
                        interpretation=interpretation,
                        display_order=index,
                    )
                    for index, (code, name, rarity, weight, question, interpretation) in enumerate(
                        CARDS, start=1
                    )
                ]
            )
        if not session.scalar(select(User.id).limit(1)):
            session.add_all([User(nickname=name, role=role) for name, role in USERS])
    with SessionLocal.begin() as session:
        users = session.scalars(select(User)).all()
        cards = session.scalars(select(CardType)).all()
        for user in users:
            for card in cards:
                balance = session.get(CardBalance, (user.id, card.id))
                if balance is None:
                    session.add(
                        CardBalance(
                            user_id=user.id,
                            card_type_id=card.id,
                            quantity=1 if user.role != Role.USER else 0,
                        )
                    )


if __name__ == "__main__":
    seed()

